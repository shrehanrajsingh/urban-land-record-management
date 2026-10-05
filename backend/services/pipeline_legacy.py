"""Main integrate_dataset orchestrator (§40)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.services.conflation.engine import run_conflation
from backend.services.conflicts.engine import detect_conflicts
from backend.services.evidence.fusion import extract_physical_evidence_stub, fuse_evidence
from backend.services.registration.engine import run_registration
from backend.services.review.workflow import (
    create_candidate_version,
    maybe_audit_auto_accept,
    qualifies_for_auto_accept,
)
from backend.services.ids import new_id
from sqlalchemy import text


def integrate_dataset(
    db: Session,
    *,
    source_dataset_id: str,
    reference_dataset_id: str,
    control_points: list[dict[str, Any]],
    method: str = "auto",
    uncertainty_envelope_m: float = 0.5,
    with_dtm: bool = False,
) -> dict[str, Any]:
    registration = run_registration(
        db,
        source_dataset_id=source_dataset_id,
        reference_dataset_id=reference_dataset_id,
        control_points=control_points,
        method=method,
        uncertainty_envelope_m=uncertainty_envelope_m,
    )

    if not registration["passed"]:
        return {
            "status": "REGISTRATION_FAILURE",
            "registration": registration,
            "matches": [],
            "conflicts": [{"type": "REGISTRATION_FAILURE"}],
        }

    # Physical evidence
    bounds_row = db.execute(
        text("SELECT ST_XMin(e) xmin, ST_YMin(e) ymin, ST_XMax(e) xmax, ST_YMax(e) ymax FROM (SELECT ST_Extent(geometry) e FROM parcel_versions) s")
    ).mappings().first()
    bounds = (
        float(bounds_row["xmin"]),
        float(bounds_row["ymin"]),
        float(bounds_row["xmax"]),
        float(bounds_row["ymax"]),
    )
    observations = extract_physical_evidence_stub(
        db, source_dataset=reference_dataset_id, parcels_bounds=bounds, with_dtm=with_dtm
    )

    conflation = run_conflation(
        db,
        source_dataset_id=source_dataset_id,
        target_dataset_id=reference_dataset_id,
        registration_run_id=registration["registration_run_id"],
        registration_uncertainty=float(registration["uncertainty_m"]),
    )

    fused = []
    for m in conflation["matches"]:
        fused.append({**m, "fused": fuse_evidence(m)})

    conflicts = detect_conflicts(
        db,
        conflation["matches"],
        registration_passed=True,
        registration_uncertainty=float(registration["uncertainty_m"]),
    )
    conflict_match_ids = {c.get("match_id") for c in conflicts["conflicts"] if c.get("match_id")}

    candidates_out = []
    for m in fused:
        high_conflict = m["id"] in conflict_match_ids
        cand = {
            "geometry_valid": True,
            "regional_registration_pass": True,
            "match_probability": m["probability"],
            "high_conflict": high_conflict,
            "authoritative_attribute_conflict": high_conflict and m.get("relation") in ("ONE_TO_ONE", "BOUNDARY_ADJUSTMENT"),
            "structural_ambiguity": m.get("relation") in ("AMBIGUOUS", "SPLIT", "MERGE") and m["probability"] < 0.9,
            "within_uncertainty_envelope": not high_conflict,
            "provenance_complete": True,
            "legal_state_unchanged": True,
            "match": m,
        }
        if qualifies_for_auto_accept(cand) and m.get("targets"):
            pid = m["targets"][0]
            geom_row = db.execute(
                text(
                    """
                    SELECT ST_AsText(geometry) AS wkt, attributes, source_ids, geometry_uncertainty_m
                    FROM parcel_versions WHERE parcel_id = :p
                    ORDER BY version DESC LIMIT 1
                    """
                ),
                {"p": pid},
            ).mappings().first()
            if geom_row:
                pv_id = create_candidate_version(
                    db,
                    parcel_id=pid,
                    geometry_wkt=geom_row["wkt"],
                    status="VALIDATED",
                    attributes=geom_row["attributes"] or {},
                    source_ids=list(set((geom_row["source_ids"] or []) + [source_dataset_id, reference_dataset_id])),
                    match_probability=m["probability"],
                    geometry_uncertainty_m=float(geom_row["geometry_uncertainty_m"] or 0.25),
                    created_by="ai-pipeline",
                    created_by_role="AI_SERVICE",
                    transformation_id=registration["transformation_id"],
                    model_run_id="matcher-1.0-logreg",
                    lineage=[
                        {"step": "source_dataset", "id": source_dataset_id},
                        {"step": "registration_run", "id": registration["registration_run_id"]},
                        {"step": "candidate_match", "id": m["id"]},
                    ],
                )
                audited = maybe_audit_auto_accept(db, pv_id)
                candidates_out.append({"parcel_id": pid, "status": "VALIDATED", "version_id": pv_id, "audited": audited})
        else:
            # Ensure review case exists for non-auto-accept without conflict already
            if m["id"] not in conflict_match_ids and m.get("relation") != "NO_MATCH":
                from backend.models.schema import ReviewCase

                rid = new_id("RC")
                db.add(
                    ReviewCase(
                        id=rid,
                        candidate_match_id=m["id"],
                        status="OPEN",
                        summary=f"Manual review for {m.get('source')} ({m.get('relation')})",
                        evidence_snapshot=m.get("fused") or m.get("evidence") or {},
                    )
                )
                db.commit()
            candidates_out.append({"match_id": m["id"], "status": "REVIEW", "relation": m.get("relation")})

    return {
        "status": "OK",
        "registration": registration,
        "observations": observations,
        "matches": fused,
        "conflicts": conflicts,
        "candidates": candidates_out,
    }
