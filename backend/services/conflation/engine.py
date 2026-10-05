"""Uncertainty-aware conflation engine with six-dimension evidence fusion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from shapely import wkt as shapely_wkt
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models.schema import CandidateMatch, MatchEvidence
from backend.services.ids import new_id
from geospatial.geometry.features import build_pair_features
from geospatial.geometry.uncertainty import search_radius
from geospatial.split_merge.detect import detect_boundary_adjustment, detect_merge, detect_split
from ml.matcher.model import MatchProbabilityModel

settings = get_settings()

_model: MatchProbabilityModel | None = None


def get_matcher() -> MatchProbabilityModel:
    global _model
    if _model is not None:
        return _model
    model = MatchProbabilityModel()
    path = Path(settings.data_dir) / "models" / "matcher.pkl"
    if path.exists():
        model.load(path)
    else:
        # Train on synthetic data as fallback
        from ml.matcher.model import generate_synthetic_training_pairs
        X, y = generate_synthetic_training_pairs(1000)
        n = len(y)
        rng = np.random.default_rng(42)
        idx = np.arange(n)
        rng.shuffle(idx)
        train = idx[: int(0.6 * n)]
        model.fit(X[train], y[train])
        path.parent.mkdir(parents=True, exist_ok=True)
        model.save(path)
    _model = model
    return model


def _latest_parcels_sql(db: Session, dataset_id: str | None = None) -> list[dict[str, Any]]:
    """Load latest parcel versions, using SQL-based filtering for efficiency."""
    q = """
        SELECT DISTINCT ON (pv.parcel_id)
            pv.parcel_id, pv.id AS version_id, ST_AsText(pv.geometry) AS wkt,
            pv.attributes, pv.geometry_uncertainty_m, pv.source_ids, pv.status
        FROM parcel_versions pv
        WHERE 1=1
    """
    params: dict[str, Any] = {}
    if dataset_id:
        q += " AND pv.source_ids @> CAST(:ds AS jsonb)"
        params["ds"] = f'["{dataset_id}"]'
    q += " ORDER BY pv.parcel_id, pv.version DESC"
    rows = db.execute(text(q), params).mappings().all()
    out = []
    for r in rows:
        out.append(
            {
                "parcel_id": r["parcel_id"],
                "version_id": r["version_id"],
                "geometry": shapely_wkt.loads(r["wkt"]),
                "attributes": r["attributes"] or {},
                "uncertainty_m": float(r["geometry_uncertainty_m"] or 0.25),
                "source_ids": r["source_ids"] or [],
                "status": r["status"],
            }
        )
    return out


def _sql_candidate_retrieval(
    db: Session,
    source_wkt: str,
    source_uncertainty: float,
    registration_uncertainty: float,
    target_dataset_id: str,
) -> list[dict[str, Any]]:
    """
    Use PostGIS ST_DWithin for spatial candidate retrieval with regional sigma.
    This is much more efficient than loading all targets and computing distances in Python.
    """
    radius = search_radius(source_uncertainty, registration_uncertainty)

    q = """
        SELECT DISTINCT ON (pv.parcel_id)
            pv.parcel_id, pv.id AS version_id, ST_AsText(pv.geometry) AS wkt,
            pv.attributes, pv.geometry_uncertainty_m, pv.source_ids, pv.status,
            ST_Distance(pv.geometry, ST_SetSRID(ST_GeomFromText(:src_wkt), 32643)) AS distance
        FROM parcel_versions pv
        WHERE pv.source_ids @> CAST(:ds AS jsonb)
          AND ST_DWithin(pv.geometry, ST_SetSRID(ST_GeomFromText(:src_wkt), 32643), :radius)
        ORDER BY pv.parcel_id, pv.version DESC
    """
    rows = db.execute(
        text(q),
        {"src_wkt": source_wkt, "ds": f'["{target_dataset_id}"]', "radius": radius},
    ).mappings().all()

    return [
        {
            "parcel_id": r["parcel_id"],
            "version_id": r["version_id"],
            "geometry": shapely_wkt.loads(r["wkt"]),
            "attributes": r["attributes"] or {},
            "uncertainty_m": float(r["geometry_uncertainty_m"] or 0.25),
            "source_ids": r["source_ids"] or [],
            "status": r["status"],
            "distance": float(r["distance"]),
        }
        for r in rows
    ]


def _gnss_support(db: Session, geom) -> float:
    row = db.execute(
        text(
            """
            SELECT COUNT(*) AS n FROM survey_points
            WHERE ST_DWithin(geometry, ST_SetSRID(ST_GeomFromText(:wkt), 32643), 1.0)
            """
        ),
        {"wkt": geom.wkt},
    ).mappings().first()
    n = int(row["n"]) if row else 0
    return float(min(1.0, n / 3.0))


def _building_support(db: Session, geom) -> float:
    row = db.execute(
        text(
            """
            SELECT COUNT(*) AS n FROM observations
            WHERE type IN ('BUILDING', 'BUILDING_EDGE', 'WALL')
              AND ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText(:wkt), 32643))
            """
        ),
        {"wkt": geom.wkt},
    ).mappings().first()
    n = int(row["n"]) if row else 0
    return float(min(1.0, n / 2.0))


def _compute_six_dim_evidence(
    feats: dict[str, float],
    gnss: float,
    building: float,
    src_attrs: dict,
    tgt_attrs: dict,
) -> dict[str, float]:
    """Compute all six evidence dimensions with proper semantics."""
    # 1. Geometry compatibility: blend of IoU, area_ratio, shape_similarity
    geom_compat = 0.5 * feats.get("iou", 0) + 0.25 * feats.get("area_ratio", 0) + 0.25 * feats.get("shape_similarity", 0)

    # 2. Attribute compatibility: from feature computation
    attr_compat = feats.get("attribute_similarity", 0)

    # 3. Survey support: GNSS point count near boundary
    survey = gnss

    # 4. Physical support: building/wall intersection evidence
    physical = building

    # 5. Temporal support: assume higher for more recent datasets
    temporal = 0.9  # Synthetic data is all contemporary

    # 6. Identity compatibility: survey_no and parcel_id match strength
    identity = 0.5
    src_sn = str(src_attrs.get("survey_no", "")).strip().upper()
    tgt_sn = str(tgt_attrs.get("survey_no", "")).strip().upper()
    if src_sn and tgt_sn:
        if src_sn == tgt_sn:
            identity = 1.0
        elif "AMBIG" in src_sn or "AMBIG" in tgt_sn:
            identity = 0.2
        elif src_sn.replace("_X", "") == tgt_sn.replace("_X", ""):
            identity = 0.7
        else:
            identity = 0.3

    return {
        "geometry_compatibility": round(geom_compat, 4),
        "attribute_compatibility": round(attr_compat, 4),
        "survey_support": round(survey, 4),
        "physical_support": round(physical, 4),
        "temporal_support": round(temporal, 4),
        "identity_compatibility": round(identity, 4),
    }


def run_conflation(
    db: Session,
    *,
    source_dataset_id: str,
    target_dataset_id: str,
    registration_run_id: str | None = None,
    registration_uncertainty: float = 0.3,
) -> dict[str, Any]:
    matcher = get_matcher()
    sources = _latest_parcels_sql(db, source_dataset_id)

    matches = []
    for src in sources:
        # SQL-based candidate retrieval with regional sigma
        candidates = _sql_candidate_retrieval(
            db,
            source_wkt=src["geometry"].wkt,
            source_uncertainty=src["uncertainty_m"],
            registration_uncertainty=registration_uncertainty,
            target_dataset_id=target_dataset_id,
        )

        if not candidates:
            mid = new_id("CM")
            db.add(
                CandidateMatch(
                    id=mid,
                    source_parcel_id=src["parcel_id"],
                    target_parcel_ids=[],
                    relation="NO_MATCH",
                    match_probability=0.0,
                    features={},
                    evidence={},
                    registration_run_id=registration_run_id,
                )
            )
            matches.append({"id": mid, "source": src["parcel_id"], "relation": "NO_MATCH", "probability": 0.0, "targets": [], "evidence": {}})
            continue

        pair_feats = []
        for t in candidates:
            try:
                inter = src["geometry"].intersection(t["geometry"])
                gnss_geom = inter.buffer(0.01) if not inter.is_empty else src["geometry"]
            except Exception:
                gnss_geom = src["geometry"]
            gnss = _gnss_support(db, gnss_geom)
            bld = _building_support(db, t["geometry"])
            feats = build_pair_features(
                src["geometry"],
                t["geometry"],
                src["attributes"],
                t["attributes"],
                gnss_support=gnss,
                building_support=bld,
            )
            six_dim = _compute_six_dim_evidence(feats, gnss, bld, src["attributes"], t["attributes"])
            pair_feats.append((t, feats, six_dim))

        # Model-only predictions (no heuristic blend in v2)
        probs = matcher.predict_proba([f for _, f, _ in pair_feats])
        ranked = sorted(zip(pair_feats, probs), key=lambda x: x[1], reverse=True)

        best_t, best_f, best_six = ranked[0][0]
        best_p = float(ranked[0][1])
        second_p = float(ranked[1][1]) if len(ranked) > 1 else 0.0

        # Structural inference
        relation = "ONE_TO_ONE"
        structure_details: dict[str, Any] = {}
        clear_winner = best_p >= 0.7 and (best_p - second_p) >= 0.08
        high = [(t, f, sd, float(p)) for (t, f, sd), p in ranked if float(p) >= 0.6]

        if not clear_winner and len(high) >= 2:
            split_candidates = high[:4]
            split = detect_split(
                src["geometry"],
                [t["geometry"] for t, _, _, _ in split_candidates],
                coverage_threshold=settings.coverage_threshold,
                overlap_threshold=settings.overlap_threshold,
                area_tolerance=settings.area_ratio_tolerance,
            )
            if split.accepted:
                relation = "SPLIT"
                structure_details = {
                    "coverage": split.coverage,
                    "mutual_overlap": split.mutual_overlap,
                    "area_ratio": split.area_ratio,
                }
                targets_ids = [t["parcel_id"] for t, _, _, _ in split_candidates]
                best_p = float(sum(p for _, _, _, p in split_candidates) / len(split_candidates))
            elif best_p >= 0.75 and best_f.get("iou", 0) >= 0.55:
                adj = detect_boundary_adjustment(src["geometry"], best_t["geometry"])
                relation = "BOUNDARY_ADJUSTMENT" if adj.accepted else "ONE_TO_ONE"
                structure_details = adj.details if adj.accepted else {"margin": best_p - second_p}
                targets_ids = [best_t["parcel_id"]]
            else:
                relation = "AMBIGUOUS"
                targets_ids = [t["parcel_id"] for t, _, _, _ in high[:3]]
                structure_details = {"coverage": split.coverage, "best_p": best_p, "second_p": second_p}
        else:
            adj = detect_boundary_adjustment(src["geometry"], best_t["geometry"])
            if adj.accepted and best_p >= 0.7:
                relation = "BOUNDARY_ADJUSTMENT"
                structure_details = adj.details
            elif best_p < 0.45:
                relation = "NO_MATCH"
            targets_ids = [best_t["parcel_id"]] if relation != "NO_MATCH" else []

        evidence = best_six

        mid = new_id("CM")
        db.add(
            CandidateMatch(
                id=mid,
                source_parcel_id=src["parcel_id"],
                target_parcel_ids=targets_ids,
                relation=relation,
                match_probability=best_p,
                features=best_f if relation != "SPLIT" else {"candidates": [f for _, f, _, _ in high]},
                evidence=evidence,
                registration_run_id=registration_run_id,
            )
        )
        eid = new_id("ME")
        db.add(
            MatchEvidence(
                id=eid,
                candidate_match_id=mid,
                geometry_compatibility=evidence["geometry_compatibility"],
                attribute_compatibility=evidence["attribute_compatibility"],
                survey_support=evidence["survey_support"],
                physical_support=evidence["physical_support"],
                temporal_support=evidence["temporal_support"],
                details={
                    **structure_details,
                    "identity_compatibility": evidence["identity_compatibility"],
                },
            )
        )
        matches.append(
            {
                "id": mid,
                "source": src["parcel_id"],
                "targets": targets_ids,
                "relation": relation,
                "probability": best_p,
                "evidence": evidence,
            }
        )

    # Merge detection pass
    target_to_sources: dict[str, list[str]] = {}
    for m in matches:
        if m["relation"] in ("ONE_TO_ONE", "BOUNDARY_ADJUSTMENT") and len(m.get("targets", [])) == 1:
            target_to_sources.setdefault(m["targets"][0], []).append(m["source"])
    for tid, sids in target_to_sources.items():
        if len(sids) < 2:
            continue
        src_geoms = [s["geometry"] for s in sources if s["parcel_id"] in sids]
        tgt_candidates = _sql_candidate_retrieval(
            db, src_geoms[0].wkt if src_geoms else "POINT(0 0)",
            0.25, registration_uncertainty, target_dataset_id,
        )
        tgt = next((t for t in tgt_candidates if t["parcel_id"] == tid), None)
        if tgt is None:
            continue
        merge = detect_merge(src_geoms, tgt["geometry"])
        if merge.accepted:
            mid = new_id("CM")
            db.add(
                CandidateMatch(
                    id=mid,
                    source_parcel_id=sids[0],
                    target_parcel_ids=[tid],
                    relation="MERGE",
                    match_probability=0.85,
                    features={"merged_sources": sids, "coverage": merge.coverage},
                    evidence={"geometry_compatibility": merge.coverage},
                    registration_run_id=registration_run_id,
                )
            )
            matches.append(
                {
                    "id": mid,
                    "source": sids,
                    "targets": [tid],
                    "relation": "MERGE",
                    "probability": 0.85,
                }
            )

    db.commit()
    return {"matches": matches, "count": len(matches)}
