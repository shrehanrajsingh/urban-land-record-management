"""Pipeline orchestration — stage-based integration pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.db import set_app_role
from backend.models.schema import PipelineRun
from backend.services.ids import new_id
from backend.services.pipeline.stages import (
    stage_conflate,
    stage_detect_conflicts,
    stage_extract,
    stage_fuse,
    stage_ingest,
    stage_register,
    stage_route,
)

# Re-export all stages
from backend.services.pipeline.stages import *  # noqa: F401,F403

# Also provide the legacy integrate_dataset for backward compat
from backend.services.pipeline_legacy import integrate_dataset  # noqa: F401

STAGE_ORDER = [
    "ingest",
    "register",
    "extract",
    "conflate",
    "fuse",
    "detect_conflicts",
    "route",
]


def run_full_pipeline(
    db: Session,
    pipeline_run_id: str,
    source_dataset_id: str,
    reference_dataset_id: str,
    method: str = "auto",
    uncertainty_envelope_m: float = 0.5,
    with_dtm: bool = False,
    control_points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run all pipeline stages in order, updating the PipelineRun record."""
    run = db.get(PipelineRun, pipeline_run_id)
    if run:
        run.status = "RUNNING"
        db.commit()

    set_app_role(db, "AI_SERVICE")

    results: dict[str, Any] = {}

    try:
        # 1. Ingest
        results["ingest"] = stage_ingest(
            db, pipeline_run_id,
            source_dataset_id=source_dataset_id,
            reference_dataset_id=reference_dataset_id,
        )

        # 2. Register
        registration = stage_register(
            db, pipeline_run_id,
            source_dataset_id=source_dataset_id,
            reference_dataset_id=reference_dataset_id,
            method=method,
            uncertainty_envelope_m=uncertainty_envelope_m,
            control_points=control_points,
        )
        results["register"] = registration

        if not registration.get("passed"):
            results["status"] = "REGISTRATION_FAILURE"
            if run:
                run.status = "FAILED"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
            return results

        # 3. Extract
        results["extract"] = stage_extract(
            db, pipeline_run_id,
            reference_dataset_id=reference_dataset_id,
            with_dtm=with_dtm,
        )

        # 4. Conflate
        conflation = stage_conflate(
            db, pipeline_run_id,
            source_dataset_id=source_dataset_id,
            reference_dataset_id=reference_dataset_id,
            registration_run_id=registration.get("registration_run_id"),
            registration_uncertainty=float(registration.get("uncertainty_m", 0.3)),
        )
        results["conflate"] = conflation

        # 5. Fuse
        fuse_result = stage_fuse(
            db, pipeline_run_id,
            matches=conflation.get("matches"),
        )
        results["fuse"] = fuse_result

        # 6. Detect conflicts
        conflicts = stage_detect_conflicts(
            db, pipeline_run_id,
            matches=conflation.get("matches"),
            registration_passed=True,
            registration_uncertainty=float(registration.get("uncertainty_m", 0.3)),
        )
        results["detect_conflicts"] = conflicts

        # 7. Route
        conflict_match_ids = {
            c.get("match_id")
            for c in conflicts.get("conflicts", [])
            if c.get("match_id")
        }
        route_result = stage_route(
            db, pipeline_run_id,
            fused_matches=fuse_result.get("fused_matches"),
            conflict_match_ids=conflict_match_ids,
            source_dataset_id=source_dataset_id,
            reference_dataset_id=reference_dataset_id,
            registration=registration,
        )
        results["route"] = route_result

        results["status"] = "COMPLETED"
        if run:
            run.status = "COMPLETED"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()

    except Exception as exc:
        results["status"] = "FAILED"
        results["error"] = str(exc)
        if run:
            run.status = "FAILED"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise

    return results
