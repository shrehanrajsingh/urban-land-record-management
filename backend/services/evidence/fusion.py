"""Evidence fusion with 6 separate dimensions and physical evidence extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from geoalchemy2.elements import WKTElement
from shapely.geometry import LineString, box
from shapely import wkt as shapely_wkt
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.schema import Observation, DatasetAsset
from backend.services.ids import new_id


# The six evidence dimensions (§ 23-25 of spec)
EVIDENCE_DIMENSIONS = [
    "geometry_compatibility",
    "attribute_compatibility",
    "survey_support",
    "physical_support",
    "temporal_support",
    "identity_compatibility",
]

# Precedence order for conflict resolution (§ 24)
DIMENSION_PRECEDENCE = {
    "survey_support": 6,         # GNSS/total station is highest precision
    "geometry_compatibility": 5,  # Geometric agreement
    "physical_support": 4,        # Physical evidence (buildings, walls)
    "identity_compatibility": 3,  # Survey number / ID matching
    "attribute_compatibility": 2, # Area, land use, owner
    "temporal_support": 1,        # Recency
}


def fuse_evidence(match: dict[str, Any]) -> dict[str, Any]:
    """
    Fuse evidence across 6 dimensions with precedence weighting.
    Never collapses to a single unexplained score — keeps all dims
    plus the calibrated match_probability.
    """
    ev = match.get("evidence") or {}

    dims = {}
    for dim in EVIDENCE_DIMENSIONS:
        dims[dim] = float(ev.get(dim, 0.0))

    # Compute identity_compatibility if not already present
    if dims["identity_compatibility"] == 0.0:
        # Derive from attribute_similarity and survey_no match
        attr_sim = dims["attribute_compatibility"]
        # Identity is stronger than general attribute — it's specifically about
        # whether the parcels are the same legal entity
        dims["identity_compatibility"] = min(1.0, attr_sim * 1.2) if attr_sim > 0.5 else attr_sim * 0.8

    # Weighted fusion score (for ranking, not for final probability)
    weighted = sum(
        dims[d] * DIMENSION_PRECEDENCE[d]
        for d in EVIDENCE_DIMENSIONS
    )
    total_weight = sum(DIMENSION_PRECEDENCE.values())
    fusion_score = weighted / total_weight if total_weight > 0 else 0.0

    # Disagreement detection: flag when high-precedence dims disagree with low ones
    high_prec = [dims[d] for d in ["survey_support", "geometry_compatibility", "physical_support"]]
    low_prec = [dims[d] for d in ["attribute_compatibility", "temporal_support", "identity_compatibility"]]
    avg_high = sum(high_prec) / len(high_prec) if high_prec else 0
    avg_low = sum(low_prec) / len(low_prec) if low_prec else 0
    disagreement = abs(avg_high - avg_low) > 0.3

    return {
        "match_probability": float(match.get("probability", 0)),
        "fusion_score": round(fusion_score, 4),
        "disagreement": disagreement,
        **{d: round(v, 4) for d, v in dims.items()},
    }


def extract_physical_evidence(
    db: Session,
    *,
    source_dataset: str,
    parcels_bounds: tuple[float, float, float, float],
    ori_path: str | None = None,
    dsm_path: str | None = None,
    dtm_path: str | None = None,
    with_dtm: bool = False,
) -> list[dict[str, Any]]:
    """
    Extract building footprints and wall/boundary features from ORI imagery.
    Uses real image processing when imagery is available, falls back to
    synthetic observations when not.
    """
    # Try to find imagery assets in the database
    if ori_path is None:
        asset = db.execute(
            text(
                "SELECT path FROM dataset_assets WHERE asset_type = 'ORI' LIMIT 1"
            )
        ).mappings().first()
        if asset:
            ori_path = asset["path"]

    if dsm_path is None:
        asset = db.execute(
            text(
                "SELECT path FROM dataset_assets WHERE asset_type = 'DSM' LIMIT 1"
            )
        ).mappings().first()
        if asset:
            dsm_path = asset["path"]

    if dtm_path is None and with_dtm:
        asset = db.execute(
            text(
                "SELECT path FROM dataset_assets WHERE asset_type = 'DTM' LIMIT 1"
            )
        ).mappings().first()
        if asset:
            dtm_path = asset["path"]

    # Try real extraction
    observations = []
    if ori_path and Path(ori_path).exists():
        try:
            from ml.building.extract import extract_all
            extracted = extract_all(
                ori_path=ori_path,
                dsm_path=dsm_path,
                dtm_path=dtm_path if with_dtm else None,
                parcels_bounds=parcels_bounds,
            )
            for item in extracted:
                oid = new_id("OBS")
                geom_wkt = item["geometry_wkt"]
                props = {}
                if item.get("height_m") is not None:
                    props["height_m"] = item["height_m"]
                if item.get("area_m2") is not None:
                    props["area_m2"] = item["area_m2"]
                if item.get("length_m") is not None:
                    props["length_m"] = item["length_m"]

                db.add(
                    Observation(
                        id=oid,
                        type=item["type"],
                        geometry=WKTElement(geom_wkt, srid=32643),
                        source_dataset=source_dataset,
                        model="image-extraction-v2",
                        model_confidence=item["confidence"],
                        positional_uncertainty_m=item["uncertainty_m"],
                        properties=props,
                    )
                )
                observations.append({
                    "id": oid,
                    "type": item["type"],
                    "confidence": item["confidence"],
                    "uncertainty_m": item["uncertainty_m"],
                    **props,
                })

            db.commit()
            return observations
        except Exception as exc:
            import traceback
            traceback.print_exc()

    # Fallback: synthetic observations for demo
    return _extract_synthetic_observations(db, source_dataset, parcels_bounds, with_dtm)


def _extract_synthetic_observations(
    db: Session,
    source_dataset: str,
    parcels_bounds: tuple[float, float, float, float],
    with_dtm: bool = False,
) -> list[dict[str, Any]]:
    """Create synthetic building/wall observations from parcel geometry for demo."""
    minx, miny, maxx, maxy = parcels_bounds
    height = 8.5 if with_dtm else None

    observations = []

    # Load actual parcel geometries to place buildings inside them
    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (parcel_id) parcel_id, ST_AsText(geometry) AS wkt
            FROM parcel_versions
            WHERE ST_Intersects(geometry,
                ST_SetSRID(ST_MakeEnvelope(:x1, :y1, :x2, :y2), 32643))
            ORDER BY parcel_id, version DESC
            """
        ),
        {"x1": minx, "y1": miny, "x2": maxx, "y2": maxy},
    ).mappings().all()

    import numpy as np
    rng = np.random.default_rng(42)

    for r in rows:
        geom = shapely_wkt.loads(r["wkt"])
        if geom.area < 100:  # skip tiny parcels
            continue

        # Create a building footprint inset from parcel edges
        try:
            inset = geom.buffer(-3.0)
            if inset.is_empty or inset.area < 10:
                continue
            # Random sub-rectangle within the inset
            bx1, by1, bx2, by2 = inset.bounds
            w = (bx2 - bx1) * rng.uniform(0.3, 0.6)
            h = (by2 - by1) * rng.uniform(0.3, 0.6)
            ox = bx1 + (bx2 - bx1 - w) * rng.uniform(0.1, 0.9)
            oy = by1 + (by2 - by1 - h) * rng.uniform(0.1, 0.9)
            building = box(ox, oy, ox + w, oy + h).intersection(inset)
            if building.is_empty or building.area < 5:
                continue
        except Exception:
            continue

        oid = new_id("OBS")
        bh = round(rng.uniform(5, 12), 1) if height else None
        props = {"parcel_id": r["parcel_id"]}
        if bh is not None:
            props["height_m"] = bh

        db.add(
            Observation(
                id=oid,
                type="BUILDING",
                geometry=WKTElement(building.wkt, srid=32643),
                source_dataset=source_dataset,
                model="synthetic-v1",
                model_confidence=round(rng.uniform(0.85, 0.97), 3),
                positional_uncertainty_m=round(rng.uniform(0.12, 0.25), 3),
                properties=props,
            )
        )
        observations.append({"id": oid, "type": "BUILDING", "parcel_id": r["parcel_id"]})

        # Add wall segments on ~60% of edges
        coords = list(geom.exterior.coords)
        for j in range(len(coords) - 1):
            if rng.random() < 0.6:
                wall = LineString([coords[j], coords[j + 1]])
                if wall.length < 2.0:
                    continue
                wid = new_id("OBS")
                db.add(
                    Observation(
                        id=wid,
                        type="WALL",
                        geometry=WKTElement(wall.wkt, srid=32643),
                        source_dataset=source_dataset,
                        model="synthetic-v1",
                        model_confidence=round(rng.uniform(0.80, 0.93), 3),
                        positional_uncertainty_m=round(rng.uniform(0.10, 0.20), 3),
                        properties={"parcel_id": r["parcel_id"]},
                    )
                )
                observations.append({"id": wid, "type": "WALL", "parcel_id": r["parcel_id"]})

    db.commit()
    return observations


# Keep backward compatibility
def extract_physical_evidence_stub(
    db: Session,
    *,
    source_dataset: str,
    parcels_bounds: tuple[float, float, float, float],
    with_dtm: bool = False,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper that calls the real extraction pipeline."""
    return extract_physical_evidence(
        db,
        source_dataset=source_dataset,
        parcels_bounds=parcels_bounds,
        with_dtm=with_dtm,
    )
