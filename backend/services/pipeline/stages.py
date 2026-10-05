"""Pipeline stages — each function is an atomic unit of the integration pipeline."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.schema import PipelineRun
from backend.services.conflation.engine import run_conflation
from backend.services.conflicts.engine import detect_conflicts
from backend.services.evidence.fusion import extract_physical_evidence_stub, fuse_evidence
from backend.services.ids import new_id
from backend.services.registration.engine import run_registration
from backend.services.review.workflow import (
    create_candidate_version,
    maybe_audit_auto_accept,
    qualifies_for_auto_accept,
)


def _update_stage(
    db: Session,
    pipeline_run_id: str,
    stage_name: str,
    status: str,
    duration_ms: float | None = None,
    counts: dict | None = None,
    output_ids: list[str] | None = None,
) -> None:
    """Update the pipeline_run's current_stage and stages JSONB."""
    run = db.get(PipelineRun, pipeline_run_id)
    if run is None:
        return
    run.current_stage = stage_name
    stages = dict(run.stages or {})
    entry = stages.get(stage_name, {})
    entry["status"] = status
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if counts is not None:
        entry["counts"] = counts
    if output_ids is not None:
        entry["output_ids"] = output_ids
    stages[stage_name] = entry
    run.stages = stages
    db.commit()


# ---------------------------------------------------------------------------
# Stage 1: Ingest
# ---------------------------------------------------------------------------

def stage_ingest(
    db: Session,
    pipeline_run_id: str,
    source_dataset_id: str,
    reference_dataset_id: str,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _update_stage(db, pipeline_run_id, "ingest", "RUNNING")

    from backend.services.ingestion.ingest import ingest_geodataframe  # noqa: F811

    # Verify datasets exist
    src = db.execute(
        text("SELECT id FROM datasets WHERE id = :id"), {"id": source_dataset_id}
    ).first()
    ref = db.execute(
        text("SELECT id FROM datasets WHERE id = :id"), {"id": reference_dataset_id}
    ).first()

    result = {
        "source_exists": src is not None,
        "reference_exists": ref is not None,
        "source_dataset_id": source_dataset_id,
        "reference_dataset_id": reference_dataset_id,
    }

    elapsed = (time.monotonic() - t0) * 1000
    _update_stage(db, pipeline_run_id, "ingest", "COMPLETED", duration_ms=elapsed)
    return result


# ---------------------------------------------------------------------------
# Stage 2: Register
# ---------------------------------------------------------------------------

def stage_register(
    db: Session,
    pipeline_run_id: str,
    source_dataset_id: str,
    reference_dataset_id: str,
    method: str = "auto",
    uncertainty_envelope_m: float = 0.5,
    control_points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _update_stage(db, pipeline_run_id, "register", "RUNNING")

    cps = control_points or []
    if not cps:
        meta = db.execute(
            text("SELECT metadata_json FROM datasets WHERE id = 'BENCH-META'")
        ).mappings().first()
        cps = (meta["metadata_json"] if meta else {}).get("control_points", [])

    registration = run_registration(
        db,
        source_dataset_id=source_dataset_id,
        reference_dataset_id=reference_dataset_id,
        control_points=cps,
        method=method,
        uncertainty_envelope_m=uncertainty_envelope_m,
    )

    elapsed = (time.monotonic() - t0) * 1000
    _update_stage(
        db, pipeline_run_id, "register", "COMPLETED",
        duration_ms=elapsed,
        output_ids=[registration.get("registration_run_id", "")],
    )
    return registration


# ---------------------------------------------------------------------------
# Stage 3: Extract physical evidence
# ---------------------------------------------------------------------------

def stage_extract(
    db: Session,
    pipeline_run_id: str,
    reference_dataset_id: str,
    with_dtm: bool = False,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _update_stage(db, pipeline_run_id, "extract", "RUNNING")

    bounds_row = db.execute(
        text(
            "SELECT ST_XMin(e) xmin, ST_YMin(e) ymin, ST_XMax(e) xmax, ST_YMax(e) ymax "
            "FROM (SELECT ST_Extent(geometry) e FROM parcel_versions) s"
        )
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

    elapsed = (time.monotonic() - t0) * 1000
    _update_stage(
        db, pipeline_run_id, "extract", "COMPLETED",
        duration_ms=elapsed,
        counts={"observations": len(observations)},
    )
    return {"observations": observations}


# ---------------------------------------------------------------------------
# Stage 4: Conflate
# ---------------------------------------------------------------------------

def stage_conflate(
    db: Session,
    pipeline_run_id: str,
    source_dataset_id: str,
    reference_dataset_id: str,
    registration_run_id: str | None = None,
    registration_uncertainty: float = 0.3,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _update_stage(db, pipeline_run_id, "conflate", "RUNNING")

    # Look up the latest registration run to get actual uncertainty
    if registration_run_id is None or registration_uncertainty <= 0.3:
        latest_reg = db.execute(
            text(
                "SELECT id, uncertainty_m FROM registration_runs "
                "WHERE source_dataset_id = :src AND reference_dataset_id = :ref "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"src": source_dataset_id, "ref": reference_dataset_id},
        ).mappings().first()
        if latest_reg:
            registration_run_id = registration_run_id or latest_reg["id"]
            registration_uncertainty = max(registration_uncertainty, float(latest_reg["uncertainty_m"] or 0.3))

    conflation = run_conflation(
        db,
        source_dataset_id=source_dataset_id,
        target_dataset_id=reference_dataset_id,
        registration_run_id=registration_run_id,
        registration_uncertainty=registration_uncertainty,
    )

    elapsed = (time.monotonic() - t0) * 1000
    _update_stage(
        db, pipeline_run_id, "conflate", "COMPLETED",
        duration_ms=elapsed,
        counts={"matches": conflation.get("count", 0)},
    )
    return conflation


# ---------------------------------------------------------------------------
# Stage 5: Fuse evidence
# ---------------------------------------------------------------------------

def stage_fuse(
    db: Session,
    pipeline_run_id: str,
    matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _update_stage(db, pipeline_run_id, "fuse", "RUNNING")

    # If no matches passed, load from DB
    if not matches:
        from backend.models.schema import CandidateMatch
        db_matches = db.query(CandidateMatch).order_by(CandidateMatch.created_at.desc()).limit(500).all()
        matches = [
            {
                "id": m.id,
                "source": m.source_parcel_id,
                "targets": m.target_parcel_ids,
                "relation": m.relation,
                "probability": m.match_probability,
                "evidence": m.evidence,
            }
            for m in db_matches
        ]
    fused = []
    for m in matches:
        fused.append({**m, "fused": fuse_evidence(m)})

    elapsed = (time.monotonic() - t0) * 1000
    _update_stage(
        db, pipeline_run_id, "fuse", "COMPLETED",
        duration_ms=elapsed,
        counts={"fused": len(fused)},
    )
    return {"fused_matches": fused}


# ---------------------------------------------------------------------------
# Stage 6: Detect conflicts
# ---------------------------------------------------------------------------

def stage_detect_conflicts(
    db: Session,
    pipeline_run_id: str,
    matches: list[dict[str, Any]] | None = None,
    registration_passed: bool = True,
    registration_uncertainty: float = 0.3,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _update_stage(db, pipeline_run_id, "detect_conflicts", "RUNNING")

    # If no matches passed, load from DB
    if not matches:
        from backend.models.schema import CandidateMatch
        db_matches = db.query(CandidateMatch).order_by(CandidateMatch.created_at.desc()).limit(500).all()
        matches = [
            {
                "id": m.id,
                "source": m.source_parcel_id,
                "targets": m.target_parcel_ids,
                "relation": m.relation,
                "probability": m.match_probability,
                "evidence": m.evidence,
            }
            for m in db_matches
        ]

    conflicts = detect_conflicts(
        db,
        matches,
        registration_passed=registration_passed,
        registration_uncertainty=registration_uncertainty,
    )

    elapsed = (time.monotonic() - t0) * 1000
    _update_stage(
        db, pipeline_run_id, "detect_conflicts", "COMPLETED",
        duration_ms=elapsed,
        counts={"conflicts": conflicts.get("count", len(conflicts.get("conflicts", [])))},
    )
    return conflicts


# ---------------------------------------------------------------------------
# Stage 7: Route (auto-accept / review)
# ---------------------------------------------------------------------------

def stage_route(
    db: Session,
    pipeline_run_id: str,
    fused_matches: list[dict[str, Any]] | None = None,
    conflict_match_ids: set[str] | None = None,
    source_dataset_id: str | None = None,
    reference_dataset_id: str | None = None,
    registration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _update_stage(db, pipeline_run_id, "route", "RUNNING")

    from backend.models.schema import CandidateMatch, ReviewCase

    conflict_match_ids = conflict_match_ids or set()
    registration = registration or {}

    # If no fused matches passed, load from DB and fuse them
    if not fused_matches:
        db_matches = db.query(CandidateMatch).order_by(CandidateMatch.created_at.desc()).limit(500).all()
        raw_matches = [
            {
                "id": m.id,
                "source": m.source_parcel_id,
                "targets": m.target_parcel_ids,
                "relation": m.relation,
                "probability": m.match_probability,
                "evidence": m.evidence,
            }
            for m in db_matches
        ]
        fused_matches = [{**m, "fused": fuse_evidence(m)} for m in raw_matches]

    # If no conflict_match_ids passed, load from DB
    if not conflict_match_ids:
        from backend.models.schema import Conflict
        conflicts = db.query(Conflict).filter(Conflict.resolved == False).all()  # noqa: E712
        for c in conflicts:
            mid = (c.details or {}).get("match_id")
            if mid:
                conflict_match_ids.add(mid)

    # Look up registration from DB if not passed
    if not registration:
        latest_reg = db.execute(
            text(
                "SELECT id AS registration_run_id, transformation_id, passed, uncertainty_m "
                "FROM registration_runs ORDER BY created_at DESC LIMIT 1"
            )
        ).mappings().first()
        if latest_reg:
            registration = dict(latest_reg)

    candidates_out: list[dict[str, Any]] = []
    for m in fused_matches:
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
                    source_ids=list(
                        set(
                            (geom_row["source_ids"] or [])
                            + [source_dataset_id or "", reference_dataset_id or ""]
                        )
                    ),
                    match_probability=m["probability"],
                    geometry_uncertainty_m=float(geom_row["geometry_uncertainty_m"] or 0.25),
                    created_by="ai-pipeline",
                    created_by_role="AI_SERVICE",
                    transformation_id=registration.get("transformation_id"),
                    model_run_id="matcher-2.0",
                    lineage=[
                        {"step": "source_dataset", "id": source_dataset_id},
                        {"step": "registration_run", "id": registration.get("registration_run_id")},
                        {"step": "candidate_match", "id": m["id"]},
                        {"step": "pipeline_run", "id": pipeline_run_id},
                    ],
                )
                audited = maybe_audit_auto_accept(db, pv_id)
                candidates_out.append({
                    "parcel_id": pid,
                    "status": "VALIDATED",
                    "version_id": pv_id,
                    "audited": audited,
                })
        else:
            if m["id"] not in conflict_match_ids and m.get("relation") != "NO_MATCH":
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
            candidates_out.append({
                "match_id": m["id"],
                "status": "REVIEW",
                "relation": m.get("relation"),
            })

    elapsed = (time.monotonic() - t0) * 1000
    _update_stage(
        db, pipeline_run_id, "route", "COMPLETED",
        duration_ms=elapsed,
        counts={"candidates": len(candidates_out)},
    )
    return {"candidates": candidates_out}
