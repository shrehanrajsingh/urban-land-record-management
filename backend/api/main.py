from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.schemas import (
    HealthResponse,
    IntegrateRequest,
    PublishAttemptRequest,
    ReviewActionRequest,
)
from backend.config import get_settings
from backend.db import get_db, set_app_role
from backend.models.schema import (
    CandidateMatch,
    Conflict,
    ParcelVersion,
    PipelineRun,
    Provenance,
    RegistrationRun,
    ReviewCase,
)
from backend.services.ids import new_id
from backend.services.pipeline import (
    STAGE_ORDER,
    integrate_dataset,
    run_full_pipeline,
    stage_conflate,
    stage_detect_conflicts,
    stage_extract,
    stage_fuse,
    stage_ingest,
    stage_register,
    stage_route,
)
from backend.services.review.workflow import apply_review_action, create_candidate_version
from backend.api.ogc.features import router as ogc_router

settings = get_settings()

app = FastAPI(
    title="Cadastral Evidence Fusion API",
    description="AI-assisted registration, conflation, conflict routing, and versioned review",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models for new endpoints
# ---------------------------------------------------------------------------

class CreatePipelineRunRequest(BaseModel):
    source_dataset_id: str = "CAD-LEGACY"
    reference_dataset_id: str = "SURVEY-2026"


class RunStageRequest(BaseModel):
    method: Optional[str] = "auto"
    uncertainty_envelope_m: Optional[float] = 0.5
    control_points: Optional[list[dict[str, Any]]] = None
    with_dtm: Optional[bool] = False
    registration_run_id: Optional[str] = None
    registration_uncertainty: Optional[float] = 0.3
    registration_passed: Optional[bool] = True
    matches: Optional[list[dict[str, Any]]] = None
    fused_matches: Optional[list[dict[str, Any]]] = None
    conflict_match_ids: Optional[list[str]] = None
    registration: Optional[dict[str, Any]] = None


class AiPublishRawRequest(BaseModel):
    parcel_id: str


def actor_role_header(x_actor_role: str | None = Header(default="REVIEWER")) -> str:
    return x_actor_role or "REVIEWER"


# ---------------------------------------------------------------------------
# Existing endpoints (preserved)
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")


@app.get("/datasets")
def list_datasets(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT id, name, source_type, crs, metadata_json, created_at FROM datasets ORDER BY created_at")).mappings().all()
    return [dict(r) for r in rows]


@app.get("/parcels/geojson")
def parcels_geojson(dataset_id: str | None = None, db: Session = Depends(get_db)):
    q = """
        SELECT DISTINCT ON (pv.parcel_id)
            pv.parcel_id, pv.version, pv.status, pv.area, pv.attributes,
            pv.match_probability, pv.geometry_uncertainty_m, pv.source_ids,
            ST_AsGeoJSON(ST_Transform(pv.geometry, 4326)) AS geom
        FROM parcel_versions pv
        WHERE 1=1
    """
    params: dict[str, Any] = {}
    if dataset_id:
        q += " AND pv.source_ids @> CAST(:ds AS jsonb)"
        params["ds"] = f'["{dataset_id}"]'
    q += " ORDER BY pv.parcel_id, pv.version DESC"
    rows = db.execute(text(q), params).mappings().all()
    features = []
    for r in rows:
        features.append(
            {
                "type": "Feature",
                "geometry": json.loads(r["geom"]),
                "properties": {
                    "parcel_id": r["parcel_id"],
                    "version": r["version"],
                    "status": r["status"],
                    "area": r["area"],
                    "match_probability": r["match_probability"],
                    "geometry_uncertainty_m": r["geometry_uncertainty_m"],
                    "source_ids": r["source_ids"],
                    **(r["attributes"] or {}),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@app.get("/observations/geojson")
def observations_geojson(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT id, type, model_confidence, positional_uncertainty_m, "
            "ST_AsGeoJSON(ST_Transform(geometry, 4326)) AS geom FROM observations"
        )
    ).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(r["geom"]),
                "properties": {
                    "id": r["id"],
                    "type": r["type"],
                    "confidence": r["model_confidence"],
                    "uncertainty_m": r["positional_uncertainty_m"],
                },
            }
            for r in rows
        ],
    }


@app.get("/survey_points/geojson")
def survey_points_geojson(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT id, point_type, uncertainty_m, properties, "
            "ST_AsGeoJSON(ST_Transform(geometry, 4326)) AS geom FROM survey_points"
        )
    ).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(r["geom"]),
                "properties": {
                    "id": r["id"],
                    "point_type": r["point_type"],
                    "uncertainty_m": r["uncertainty_m"],
                    **(r["properties"] or {}),
                },
            }
            for r in rows
        ],
    }


@app.get("/control_points")
def get_control_points(db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT metadata_json FROM datasets WHERE id = 'BENCH-META'")
    ).mappings().first()
    if not row:
        return {"control_points": []}
    return {"control_points": (row["metadata_json"] or {}).get("control_points", [])}


@app.post("/pipelines/integrate")
def pipeline_integrate(body: IntegrateRequest, db: Session = Depends(get_db)):
    """Backward-compatible endpoint that now uses the staged pipeline internally."""
    cps = body.control_points
    if not cps:
        meta = db.execute(text("SELECT metadata_json FROM datasets WHERE id = 'BENCH-META'")).mappings().first()
        cps = (meta["metadata_json"] if meta else {}).get("control_points", [])
    if not cps:
        raise HTTPException(400, "No control points available; seed benchmark first")

    # Create a pipeline run for tracking
    run_id = new_id("PLR")
    db.add(PipelineRun(
        id=run_id,
        source_dataset_id=body.source_dataset_id,
        reference_dataset_id=body.reference_dataset_id,
    ))
    db.commit()

    set_app_role(db, "AI_SERVICE")
    try:
        result = run_full_pipeline(
            db,
            pipeline_run_id=run_id,
            source_dataset_id=body.source_dataset_id,
            reference_dataset_id=body.reference_dataset_id,
            method=body.method,
            uncertainty_envelope_m=body.uncertainty_envelope_m,
            with_dtm=body.with_dtm,
            control_points=cps,
        )
    except Exception as exc:
        raise HTTPException(500, f"Pipeline failed: {exc}") from exc
    result["pipeline_run_id"] = run_id
    return result


@app.get("/registration_runs/{run_id}")
def get_registration_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(RegistrationRun, run_id)
    if not run:
        raise HTTPException(404, "not found")
    return {
        "id": run.id,
        "passed": run.passed,
        "global_rmse": run.global_rmse,
        "regional_stats": run.regional_stats,
        "residual_heatmap": run.residual_heatmap,
        "uncertainty_m": run.uncertainty_m,
        "source_dataset_id": run.source_dataset_id,
        "reference_dataset_id": run.reference_dataset_id,
        "transformation_id": run.transformation_id,
    }


@app.get("/registration_runs")
def list_registration_runs(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT id, passed, global_rmse, uncertainty_m, regional_stats, residual_heatmap, created_at "
            "FROM registration_runs ORDER BY created_at DESC LIMIT 20"
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/matches")
def list_matches(db: Session = Depends(get_db)):
    rows = db.query(CandidateMatch).order_by(CandidateMatch.created_at.desc()).limit(200).all()
    return [
        {
            "id": m.id,
            "source_parcel_id": m.source_parcel_id,
            "target_parcel_ids": m.target_parcel_ids,
            "relation": m.relation,
            "match_probability": m.match_probability,
            "features": m.features,
            "evidence": m.evidence,
        }
        for m in rows
    ]


@app.get("/matches/geojson")
def matches_geojson(db: Session = Depends(get_db)):
    """GeoJSON LineStrings connecting source→target parcel centroids for map display."""
    rows = db.execute(text("""
        SELECT cm.id, cm.source_parcel_id, cm.target_parcel_ids,
               cm.relation, cm.match_probability,
               ST_AsGeoJSON(ST_Transform(ST_Centroid(src.geometry), 4326)) AS src_centroid,
               src.parcel_id AS src_pid
        FROM candidate_matches cm
        JOIN parcel_versions src ON src.parcel_id = cm.source_parcel_id
            AND src.version = (SELECT MAX(v.version) FROM parcel_versions v WHERE v.parcel_id = src.parcel_id)
        ORDER BY cm.created_at DESC
        LIMIT 200
    """)).mappings().all()

    features = []
    # Pre-fetch target centroids
    target_ids = set()
    for r in rows:
        for tid in (r["target_parcel_ids"] or []):
            target_ids.add(tid)

    target_centroids = {}
    if target_ids:
        tgt_rows = db.execute(text("""
            SELECT DISTINCT ON (pv.parcel_id)
                pv.parcel_id,
                ST_AsGeoJSON(ST_Transform(ST_Centroid(pv.geometry), 4326)) AS centroid
            FROM parcel_versions pv
            WHERE pv.parcel_id = ANY(:ids)
            ORDER BY pv.parcel_id, pv.version DESC
        """), {"ids": list(target_ids)}).mappings().all()
        for tr in tgt_rows:
            target_centroids[tr["parcel_id"]] = json.loads(tr["centroid"])

    for r in rows:
        src_pt = json.loads(r["src_centroid"])
        for tid in (r["target_parcel_ids"] or []):
            tgt_pt = target_centroids.get(tid)
            if not tgt_pt:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        src_pt["coordinates"],
                        tgt_pt["coordinates"],
                    ],
                },
                "properties": {
                    "match_id": r["id"],
                    "source": r["source_parcel_id"],
                    "target": tid,
                    "relation": r["relation"],
                    "probability": r["match_probability"],
                },
            })
    return {"type": "FeatureCollection", "features": features}


@app.get("/conflicts")
def list_conflicts(db: Session = Depends(get_db)):
    rows = db.query(Conflict).order_by(Conflict.created_at.desc()).limit(200).all()
    return [
        {
            "id": c.id,
            "conflict_type": c.conflict_type,
            "severity": c.severity,
            "parcel_ids": c.parcel_ids,
            "description": c.description,
            "details": c.details,
            "resolved": c.resolved,
        }
        for c in rows
    ]


@app.get("/conflicts/geojson")
def conflicts_geojson(db: Session = Depends(get_db)):
    """GeoJSON polygons for conflicts — uses conflict.geometry if available, else unions parcel geometries."""
    rows = db.execute(text("""
        SELECT c.id, c.conflict_type, c.severity, c.parcel_ids, c.resolved,
               ST_AsGeoJSON(ST_Transform(c.geometry, 4326)) AS geom
        FROM conflicts c
        ORDER BY c.created_at DESC
        LIMIT 200
    """)).mappings().all()

    features = []
    # Gather parcel IDs for conflicts without their own geometry
    needs_geom = {}
    for r in rows:
        if r["geom"]:
            features.append({
                "type": "Feature",
                "geometry": json.loads(r["geom"]),
                "properties": {
                    "conflict_id": r["id"],
                    "type": r["conflict_type"],
                    "severity": r["severity"],
                    "resolved": r["resolved"],
                },
            })
        else:
            needs_geom[r["id"]] = r

    # For conflicts without geometry, build polygons from referenced parcels
    if needs_geom:
        all_pids = set()
        for r in needs_geom.values():
            for pid in (r["parcel_ids"] or []):
                all_pids.add(pid)

        if all_pids:
            parcel_geoms = {}
            pg_rows = db.execute(text("""
                SELECT DISTINCT ON (pv.parcel_id)
                    pv.parcel_id,
                    ST_AsGeoJSON(ST_Transform(pv.geometry, 4326)) AS geom
                FROM parcel_versions pv
                WHERE pv.parcel_id = ANY(:ids)
                ORDER BY pv.parcel_id, pv.version DESC
            """), {"ids": list(all_pids)}).mappings().all()
            for pr in pg_rows:
                parcel_geoms[pr["parcel_id"]] = json.loads(pr["geom"])

            for cid, r in needs_geom.items():
                for pid in (r["parcel_ids"] or []):
                    geom = parcel_geoms.get(pid)
                    if geom:
                        features.append({
                            "type": "Feature",
                            "geometry": geom,
                            "properties": {
                                "conflict_id": cid,
                                "parcel_id": pid,
                                "type": r["conflict_type"],
                                "severity": r["severity"],
                                "resolved": r["resolved"],
                            },
                        })

    return {"type": "FeatureCollection", "features": features}


@app.get("/parcels/registered/geojson")
def registered_parcels_geojson(db: Session = Depends(get_db)):
    """Return only parcels that have been through registration (version >= 2 with stage=registered)."""
    rows = db.execute(text("""
        SELECT pv.parcel_id, pv.version, pv.status, pv.area,
               pv.geometry_uncertainty_m, pv.attributes,
               ST_AsGeoJSON(ST_Transform(pv.geometry, 4326)) AS geom
        FROM parcel_versions pv
        WHERE pv.version >= 2
          AND (pv.attributes->>'stage') = 'registered'
        ORDER BY pv.parcel_id, pv.version DESC
    """)).mappings().all()
    seen = set()
    features = []
    for r in rows:
        if r["parcel_id"] in seen:
            continue
        seen.add(r["parcel_id"])
        features.append({
            "type": "Feature",
            "geometry": json.loads(r["geom"]),
            "properties": {
                "parcel_id": r["parcel_id"],
                "version": r["version"],
                "status": r["status"],
                "area": r["area"],
                "uncertainty_m": r["geometry_uncertainty_m"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


@app.get("/review_cases")
def list_review_cases(db: Session = Depends(get_db)):
    rows = db.query(ReviewCase).order_by(ReviewCase.created_at.desc()).limit(200).all()
    return [
        {
            "id": r.id,
            "conflict_id": r.conflict_id,
            "candidate_match_id": r.candidate_match_id,
            "parcel_version_id": r.parcel_version_id,
            "status": r.status,
            "summary": r.summary,
            "evidence_snapshot": r.evidence_snapshot,
        }
        for r in rows
    ]


@app.get("/review_cases/{case_id}")
def get_review_case(case_id: str, db: Session = Depends(get_db)):
    case = db.get(ReviewCase, case_id)
    if not case:
        raise HTTPException(404, "not found")
    conflict = db.get(Conflict, case.conflict_id) if case.conflict_id else None
    match = db.get(CandidateMatch, case.candidate_match_id) if case.candidate_match_id else None
    reg = None
    if match and match.registration_run_id:
        reg = db.get(RegistrationRun, match.registration_run_id)
    return {
        "id": case.id,
        "status": case.status,
        "summary": case.summary,
        "evidence_snapshot": case.evidence_snapshot,
        "conflict": None
        if not conflict
        else {
            "id": conflict.id,
            "type": conflict.conflict_type,
            "description": conflict.description,
            "parcel_ids": conflict.parcel_ids,
            "details": conflict.details,
        },
        "match": None
        if not match
        else {
            "id": match.id,
            "source_parcel_id": match.source_parcel_id,
            "target_parcel_ids": match.target_parcel_ids,
            "relation": match.relation,
            "match_probability": match.match_probability,
            "evidence": match.evidence,
            "features": match.features,
        },
        "registration": None
        if not reg
        else {
            "id": reg.id,
            "passed": reg.passed,
            "uncertainty_m": reg.uncertainty_m,
            "regional_stats": reg.regional_stats,
            "global_rmse": reg.global_rmse,
        },
    }


@app.post("/review_cases/{case_id}/actions")
def post_review_action(case_id: str, body: ReviewActionRequest, db: Session = Depends(get_db)):
    try:
        return apply_review_action(
            db,
            review_case_id=case_id,
            action=body.action,
            actor=body.actor,
            actor_role=body.actor_role,
            notes=body.notes,
            payload=body.payload,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@app.get("/parcels/{parcel_id}/versions")
def parcel_versions(parcel_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(ParcelVersion)
        .filter(ParcelVersion.parcel_id == parcel_id)
        .order_by(ParcelVersion.version.asc())
        .all()
    )
    return [
        {
            "id": v.id,
            "parcel_id": v.parcel_id,
            "version": v.version,
            "status": v.status,
            "area": v.area,
            "attributes": v.attributes,
            "source_ids": v.source_ids,
            "match_probability": v.match_probability,
            "geometry_uncertainty_m": v.geometry_uncertainty_m,
            "created_by": v.created_by,
            "created_by_role": v.created_by_role,
            "created_at": v.created_at,
        }
        for v in rows
    ]


@app.get("/parcels/{parcel_id}/provenance")
def parcel_provenance(parcel_id: str, db: Session = Depends(get_db)):
    versions = (
        db.query(ParcelVersion)
        .filter(ParcelVersion.parcel_id == parcel_id)
        .order_by(ParcelVersion.version.asc())
        .all()
    )
    out = []
    for v in versions:
        prov = db.query(Provenance).filter(Provenance.parcel_version_id == v.id).all()
        out.append(
            {
                "parcel_version": f"{parcel_id}-v{v.version}",
                "status": v.status,
                "provenance": [
                    {
                        "derived_from": p.derived_from,
                        "lineage": p.lineage,
                        "registration_run_id": p.registration_run_id,
                        "match_model": p.match_model,
                        "review_case_id": p.review_case_id,
                    }
                    for p in prov
                ],
            }
        )
    return out


@app.post("/demo/ai_publish")
def demo_ai_publish(body: PublishAttemptRequest, db: Session = Depends(get_db)):
    """TC15: AI_SERVICE → PUBLISHED must fail at DB/application layer."""
    geom_row = db.execute(
        text(
            """
            SELECT ST_AsText(geometry) AS wkt, attributes, source_ids, geometry_uncertainty_m
            FROM parcel_versions WHERE parcel_id = :p
            ORDER BY version DESC LIMIT 1
            """
        ),
        {"p": body.parcel_id},
    ).mappings().first()
    if not geom_row:
        raise HTTPException(404, "parcel not found")
    try:
        create_candidate_version(
            db,
            parcel_id=body.parcel_id,
            geometry_wkt=geom_row["wkt"],
            status="PUBLISHED",
            attributes=geom_row["attributes"] or {},
            source_ids=list(geom_row["source_ids"] or []),
            match_probability=0.99,
            geometry_uncertainty_m=float(geom_row["geometry_uncertainty_m"] or 0.2),
            created_by=body.actor,
            created_by_role=body.actor_role,
        )
    except PermissionError as e:
        return {"ok": False, "rejected": True, "layer": "application", "detail": str(e)}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return {"ok": False, "rejected": True, "layer": "database", "detail": str(e)}
    return {"ok": True, "rejected": False, "detail": "UNEXPECTED: publish succeeded"}


@app.post("/demo/ai_publish_raw")
def demo_ai_publish_raw(body: AiPublishRawRequest, db: Session = Depends(get_db)):
    """Execute raw SQL INSERT with status='PUBLISHED' under SET ROLE ai_service.

    The DB trigger should reject this, demonstrating the defense-in-depth
    enforcement that AI_SERVICE cannot publish directly.
    """
    set_app_role(db, "AI_SERVICE")
    try:
        pv_id = new_id("PV")
        db.execute(
            text(
                """
                INSERT INTO parcel_versions
                    (id, parcel_id, version, geometry, status, created_by, created_by_role)
                SELECT
                    :pv_id,
                    :parcel_id,
                    COALESCE(MAX(version), 0) + 1,
                    (SELECT geometry FROM parcel_versions WHERE parcel_id = :parcel_id ORDER BY version DESC LIMIT 1),
                    'PUBLISHED',
                    'ai-pipeline',
                    'AI_SERVICE'
                FROM parcel_versions WHERE parcel_id = :parcel_id
                """
            ),
            {"pv_id": pv_id, "parcel_id": body.parcel_id},
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return {
            "ok": False,
            "rejected": True,
            "layer": "database_trigger",
            "detail": str(e),
        }
    return {"ok": True, "rejected": False, "detail": "UNEXPECTED: raw publish succeeded"}


@app.get("/demo/state")
def demo_state(db: Session = Depends(get_db)):
    """Aggregate state for the competition demo UI."""
    counts = {}
    for table in ("parcels", "parcel_versions", "candidate_matches", "conflicts", "review_cases", "observations"):
        counts[table] = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    latest_reg = db.execute(
        text("SELECT id, passed, global_rmse, uncertainty_m FROM registration_runs ORDER BY created_at DESC LIMIT 1")
    ).mappings().first()
    open_cases = db.execute(
        text("SELECT id, summary, status FROM review_cases WHERE status = 'OPEN' ORDER BY created_at DESC LIMIT 10")
    ).mappings().all()
    return {
        "counts": counts,
        "latest_registration": dict(latest_reg) if latest_reg else None,
        "open_review_cases": [dict(r) for r in open_cases],
    }


# ---------------------------------------------------------------------------
# New pipeline_runs endpoints
# ---------------------------------------------------------------------------

@app.post("/pipeline_runs")
def create_pipeline_run(body: CreatePipelineRunRequest, db: Session = Depends(get_db)):
    """Create a new pipeline run record."""
    run_id = new_id("PLR")
    run = PipelineRun(
        id=run_id,
        source_dataset_id=body.source_dataset_id,
        reference_dataset_id=body.reference_dataset_id,
    )
    db.add(run)
    db.commit()
    return {
        "id": run_id,
        "status": "PENDING",
        "source_dataset_id": body.source_dataset_id,
        "reference_dataset_id": body.reference_dataset_id,
    }


@app.post("/pipeline_runs/{run_id}/stages/{stage}")
def run_stage(run_id: str, stage: str, body: RunStageRequest, db: Session = Depends(get_db)):
    """Execute a single pipeline stage."""
    run = db.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(404, "pipeline run not found")
    if stage not in STAGE_ORDER:
        raise HTTPException(400, f"Unknown stage '{stage}'. Valid: {STAGE_ORDER}")

    set_app_role(db, "AI_SERVICE")

    stage_funcs = {
        "ingest": lambda: stage_ingest(
            db, run_id,
            source_dataset_id=run.source_dataset_id,
            reference_dataset_id=run.reference_dataset_id,
        ),
        "register": lambda: stage_register(
            db, run_id,
            source_dataset_id=run.source_dataset_id,
            reference_dataset_id=run.reference_dataset_id,
            method=body.method or "auto",
            uncertainty_envelope_m=body.uncertainty_envelope_m or 0.5,
            control_points=body.control_points,
        ),
        "extract": lambda: stage_extract(
            db, run_id,
            reference_dataset_id=run.reference_dataset_id,
            with_dtm=body.with_dtm or False,
        ),
        "conflate": lambda: stage_conflate(
            db, run_id,
            source_dataset_id=run.source_dataset_id,
            reference_dataset_id=run.reference_dataset_id,
            registration_run_id=body.registration_run_id,
            registration_uncertainty=body.registration_uncertainty or 0.3,
        ),
        "fuse": lambda: stage_fuse(
            db, run_id,
            matches=body.matches,
        ),
        "detect_conflicts": lambda: stage_detect_conflicts(
            db, run_id,
            matches=body.matches,
            registration_passed=body.registration_passed if body.registration_passed is not None else True,
            registration_uncertainty=body.registration_uncertainty or 0.3,
        ),
        "route": lambda: stage_route(
            db, run_id,
            fused_matches=body.fused_matches,
            conflict_match_ids=set(body.conflict_match_ids) if body.conflict_match_ids else None,
            source_dataset_id=run.source_dataset_id,
            reference_dataset_id=run.reference_dataset_id,
            registration=body.registration,
        ),
    }

    try:
        result = stage_funcs[stage]()
    except Exception as exc:
        raise HTTPException(500, f"Stage '{stage}' failed: {exc}") from exc
    return {"stage": stage, "result": result}


@app.post("/pipeline_runs/{run_id}/run_all")
def run_all_stages(run_id: str, db: Session = Depends(get_db)):
    """Run all pipeline stages in order for the given pipeline run."""
    run = db.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(404, "pipeline run not found")

    # Fetch control points
    meta = db.execute(
        text("SELECT metadata_json FROM datasets WHERE id = 'BENCH-META'")
    ).mappings().first()
    cps = (meta["metadata_json"] if meta else {}).get("control_points", [])

    set_app_role(db, "AI_SERVICE")
    try:
        result = run_full_pipeline(
            db,
            pipeline_run_id=run_id,
            source_dataset_id=run.source_dataset_id,
            reference_dataset_id=run.reference_dataset_id,
            control_points=cps,
        )
    except Exception as exc:
        raise HTTPException(500, f"Pipeline failed: {exc}") from exc
    return result


@app.get("/pipeline_runs/{run_id}")
def get_pipeline_run(run_id: str, db: Session = Depends(get_db)):
    """Get the status and details of a pipeline run."""
    run = db.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(404, "pipeline run not found")
    return {
        "id": run.id,
        "status": run.status,
        "current_stage": run.current_stage,
        "stages": run.stages,
        "source_dataset_id": run.source_dataset_id,
        "reference_dataset_id": run.reference_dataset_id,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
    }


@app.get("/pipeline_runs")
def list_pipeline_runs(db: Session = Depends(get_db)):
    """List recent pipeline runs."""
    rows = (
        db.query(PipelineRun)
        .order_by(PipelineRun.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "status": r.status,
            "current_stage": r.current_stage,
            "stages": r.stages,
            "source_dataset_id": r.source_dataset_id,
            "reference_dataset_id": r.reference_dataset_id,
            "created_at": r.created_at,
            "completed_at": r.completed_at,
        }
        for r in rows
    ]


@app.get("/pipeline_runs/{run_id}/registration_heatmap")
def get_pipeline_registration_heatmap(run_id: str, db: Session = Depends(get_db)):
    """Return the registration residual heatmap for a pipeline run."""
    pipeline = db.get(PipelineRun, run_id)
    if not pipeline:
        raise HTTPException(404, "Pipeline run not found")
    # Find latest registration run for these datasets
    reg = db.execute(
        text(
            "SELECT id, residual_heatmap, global_rmse, uncertainty_m, regional_stats "
            "FROM registration_runs "
            "WHERE source_dataset_id = :src AND reference_dataset_id = :ref "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"src": pipeline.source_dataset_id, "ref": pipeline.reference_dataset_id},
    ).mappings().first()
    if not reg or not reg["residual_heatmap"]:
        return {"heatmap": None, "message": "No registration heatmap available yet"}
    return {
        "heatmap": reg["residual_heatmap"],
        "global_rmse": reg["global_rmse"],
        "uncertainty_m": reg["uncertainty_m"],
        "regional_stats": reg["regional_stats"],
        "registration_run_id": reg["id"],
    }


# ---------------------------------------------------------------------------
# OGC API Features
# ---------------------------------------------------------------------------
app.include_router(ogc_router)


# ---------------------------------------------------------------------------
# Demo reset
# ---------------------------------------------------------------------------

@app.post("/demo/reset")
def demo_reset(db: Session = Depends(get_db)):
    """Truncate all tables and re-seed the benchmark data."""
    tables = [
        "audit_events", "provenance", "review_actions", "review_cases",
        "conflicts", "match_evidence", "candidate_matches",
        "pipeline_runs", "registration_runs", "transformations",
        "observations", "survey_points", "parcel_versions", "parcels",
        "dataset_assets", "datasets", "model_runs",
    ]
    for t in tables:
        try:
            db.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
        except Exception:
            db.rollback()
    db.commit()

    # Re-seed
    import sys as _sys
    from pathlib import Path as _Path
    _ROOT = _Path(__file__).resolve().parents[2]
    _sys.path.insert(0, str(_ROOT))

    try:
        from scripts.seed_benchmark import main as seed_main
        seed_main()
    except Exception as exc:
        return {"status": "error", "message": f"Re-seed failed: {exc}"}

    return {"status": "ok", "message": "Demo reset complete"}


# ---------------------------------------------------------------------------
# Benchmark evaluation
# ---------------------------------------------------------------------------

@app.get("/benchmark/evaluate")
def benchmark_evaluate(db: Session = Depends(get_db)):
    """Compute benchmark metrics against ground truth."""
    meta = db.execute(
        text("SELECT metadata_json FROM datasets WHERE id = 'BENCH-META'")
    ).mappings().first()
    if not meta:
        raise HTTPException(404, "Benchmark not seeded")

    ground_truth = (meta["metadata_json"] or {}).get("ground_truth", {})
    correspondences = ground_truth.get("correspondences", [])
    injected_conflicts = ground_truth.get("injected_conflicts", [])

    # Gather matches
    matches_q = db.query(CandidateMatch).all()
    predicted = [
        {
            "source_parcel_id": m.source_parcel_id,
            "target_parcel_ids": m.target_parcel_ids,
            "relation": m.relation,
        }
        for m in matches_q
    ]

    # Gather conflicts
    conflicts_q = db.query(Conflict).all()
    detected = [
        {"parcel_ids": c.parcel_ids, "conflict_type": c.conflict_type}
        for c in conflicts_q
    ]

    from benchmark.evaluation.metrics import (
        relation_accuracy,
        conflict_detection_metrics,
        registration_quality,
    )

    rel_acc = relation_accuracy(predicted, correspondences)
    conf_det = conflict_detection_metrics(detected, injected_conflicts)

    # Registration quality
    latest_reg = db.execute(
        text("SELECT regional_stats FROM registration_runs ORDER BY created_at DESC LIMIT 1")
    ).mappings().first()
    reg_qual = registration_quality(
        latest_reg["regional_stats"] if latest_reg else {},
        uncertainty_threshold=0.5,
    )

    return {
        "relation_accuracy": rel_acc,
        "conflict_detection": conf_det,
        "registration_quality": reg_qual,
        "ground_truth_correspondences": len(correspondences),
        "ground_truth_conflicts": len(injected_conflicts),
        "total_matches": len(predicted),
        "total_conflicts_detected": len(detected),
    }


# ---------------------------------------------------------------------------
# Audit events & model runs
# ---------------------------------------------------------------------------

@app.get("/audit_events")
def list_audit_events(db: Session = Depends(get_db)):
    """List recent audit events."""
    rows = db.execute(
        text(
            "SELECT id, event_type, actor, actor_role, entity_type, entity_id, details, created_at "
            "FROM audit_events ORDER BY created_at DESC LIMIT 100"
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/model_runs")
def list_model_runs(db: Session = Depends(get_db)):
    """List model training/inference runs."""
    rows = db.execute(
        text(
            "SELECT id, model_name, model_version, params, metrics, created_at "
            "FROM model_runs ORDER BY created_at DESC LIMIT 20"
        )
    ).mappings().all()
    return [dict(r) for r in rows]
