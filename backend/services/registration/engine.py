"""Registration service: escalation-based whole-layer transforms + regional validation."""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd
import numpy as np
from shapely import wkt as shapely_wkt
from shapely.geometry import box, mapping
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import set_app_role
from backend.models.schema import Conflict, RegistrationRun, ReviewCase, Transformation
from backend.services.ids import new_id
from geospatial.registration.escalation import (
    apply_registration_as_new_versions,
    register_with_escalation,
)
from geospatial.registration.transforms import (
    ControlPoint,
    TransformResult,
    apply_matrix,
    compute_residuals,
    regional_stats,
    residual_heatmap_grid,
    transform_layer,
)

logger = logging.getLogger(__name__)


def _load_geodataframe(db: Session, dataset_id: str) -> gpd.GeoDataFrame:
    """Load the latest parcel-version geometries for a dataset into a GeoDataFrame."""
    rows = db.execute(
        text(
            """
            SELECT pv.id, pv.parcel_id, ST_AsText(pv.geometry) AS wkt
            FROM parcel_versions pv
            WHERE pv.source_ids @> CAST(:ds AS jsonb)
              AND pv.version = (
                SELECT MAX(pv2.version) FROM parcel_versions pv2
                WHERE pv2.parcel_id = pv.parcel_id
              )
            """
        ),
        {"ds": f'["{dataset_id}"]'},
    ).fetchall()

    geoms = [shapely_wkt.loads(r.wkt) for r in rows]
    ids = [r.id for r in rows]
    parcel_ids = [r.parcel_id for r in rows]

    return gpd.GeoDataFrame(
        {"pv_id": ids, "parcel_id": parcel_ids},
        geometry=geoms,
        crs="EPSG:32643",
    )


def _heatmap_as_geojson(heatmap: dict[str, Any]) -> dict[str, Any]:
    """Convert residual heatmap grid cells into GeoJSON FeatureCollection (WGS84)."""
    from pyproj import Transformer

    utm_to_wgs = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    features = []
    for cell in heatmap.get("cells", []):
        bounds = cell.get("bounds")
        if bounds is None:
            continue
        x0, y0, x1, y1 = bounds
        # Reproject corners from UTM 32643 to WGS84
        lon0, lat0 = utm_to_wgs.transform(x0, y0)
        lon1, lat1 = utm_to_wgs.transform(x1, y1)
        geom = mapping(box(lon0, lat0, lon1, lat1))
        # Normalize residual to [0, 1] for styling
        mean_res = cell.get("mean_residual", 0) or 0
        p95 = cell.get("p95", 0) or 0
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "i": cell["i"],
                "j": cell["j"],
                "n": cell["n"],
                "mean_residual": mean_res,
                "p95": p95,
                "residual": min(mean_res / max(p95, 0.01), 1.0),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _residual_vectors(
    result: TransformResult,
    correspondences: list[tuple[tuple[float, float], tuple[float, float], str]] | None = None,
) -> list[dict[str, Any]]:
    """Build a list of residual vectors for visualisation."""
    if correspondences is None or result.matrix is None:
        return []
    vectors = []
    src = np.array([c[0] for c in correspondences], dtype=float)
    dst = np.array([c[1] for c in correspondences], dtype=float)
    residuals = compute_residuals(src, dst, result.matrix)
    from geospatial.registration.transforms import apply_matrix_coords
    predicted = apply_matrix_coords(src, result.matrix)
    for i in range(len(src)):
        vectors.append({
            "from": [float(src[i, 0]), float(src[i, 1])],
            "to": [float(predicted[i, 0]), float(predicted[i, 1])],
            "residual": float(residuals[i]),
        })
    return vectors


def run_registration(
    db: Session,
    *,
    source_dataset_id: str,
    reference_dataset_id: str,
    control_points: list[dict[str, Any]] | None = None,
    method: str = "auto",
    uncertainty_envelope_m: float = 0.5,
    apply_to_parcel_versions: bool = True,
) -> dict[str, Any]:
    """Run the full registration pipeline with escalation.

    1. Load source and target GeoDataFrames from the DB.
    2. Call register_with_escalation.
    3. Store Transformation and RegistrationRun records.
    4. Call apply_registration_as_new_versions to INSERT v2 versions (never UPDATE v1).
    5. If registration fails, create a REGISTRATION_FAILURE conflict + review case.
    6. Store residual heatmap as GeoJSON and residual vectors.
    7. Return dict with 'escalation_log' showing which methods were tried.
    """
    set_app_role(db, "SYSTEM")

    # 1. Load GeoDataFrames
    source_gdf = _load_geodataframe(db, source_dataset_id)
    target_gdf = _load_geodataframe(db, reference_dataset_id)

    # 2. Escalation-based registration
    result = register_with_escalation(
        source_gdf,
        target_gdf,
        uncertainty_envelope_m=uncertainty_envelope_m,
        control_points=control_points,
    )

    # Extract escalation log from params
    escalation_log = result.params.get("escalation_log", [])

    # 3. Store Transformation record
    tid = new_id("TR")
    db.add(
        Transformation(
            id=tid,
            method=result.method,
            params={
                **{k: v for k, v in result.params.items() if k != "escalation_log"},
                "matrix": result.matrix.tolist() if result.matrix is not None else None,
                "escalation_log": escalation_log,
            },
            source_crs="EPSG:32643",
            target_crs="EPSG:32643",
        )
    )
    db.flush()

    # Build GeoJSON heatmap and residual vectors
    heatmap_geojson = _heatmap_as_geojson(result.residual_heatmap)

    # Build correspondences for residual vectors (from control_points if provided)
    correspondences = None
    if control_points:
        correspondences = [
            (
                (float(cp["source"][0]), float(cp["source"][1])),
                (float(cp["target"][0]), float(cp["target"][1])),
                cp.get("region", "default"),
            )
            for cp in control_points
        ]
    vectors = _residual_vectors(result, correspondences)

    # Augmented regional stats with vectors
    regional_stats_augmented = {
        **result.regional_stats,
        "_residual_vectors": vectors,
    }

    # Store RegistrationRun
    rid = new_id("REG")
    db.add(
        RegistrationRun(
            id=rid,
            source_dataset_id=source_dataset_id,
            reference_dataset_id=reference_dataset_id,
            transformation_id=tid,
            passed=result.passed,
            global_rmse=result.global_rmse,
            regional_stats=regional_stats_augmented,
            residual_heatmap=heatmap_geojson,
            uncertainty_m=result.uncertainty_m,
        )
    )

    # 4. Apply transform — INSERT new versions, never UPDATE v1
    transformed_count = 0
    if result.passed and apply_to_parcel_versions:
        transformed_count = apply_registration_as_new_versions(
            db, result, source_dataset_id, tid
        )

    # 5. Handle failure — create conflict + review case
    conflict_id = None
    review_id = None
    if not result.passed:
        conflict_id = new_id("CF")
        db.add(
            Conflict(
                id=conflict_id,
                conflict_type="REGISTRATION_FAILURE",
                severity="HIGH",
                parcel_ids=[],
                description="Regional registration residuals exceed uncertainty envelope",
                details={
                    "global_rmse": result.global_rmse,
                    "regional_stats": result.regional_stats,
                    "escalation_log": escalation_log,
                },
            )
        )
        review_id = new_id("RC")
        db.add(
            ReviewCase(
                id=review_id,
                conflict_id=conflict_id,
                status="OPEN",
                summary="Manual control-point selection required — all escalation methods failed",
                evidence_snapshot={
                    "registration_run_id": rid,
                    "escalation_log": escalation_log,
                },
            )
        )

    db.commit()

    # 7. Return result dict with escalation_log
    return {
        "registration_run_id": rid,
        "transformation_id": tid,
        "passed": result.passed,
        "global_rmse": result.global_rmse,
        "regional_stats": regional_stats_augmented,
        "residual_heatmap": heatmap_geojson,
        "uncertainty_m": result.uncertainty_m,
        "method": result.method,
        "params": result.params,
        "transformed_count": transformed_count,
        "conflict_id": conflict_id,
        "review_case_id": review_id,
        "escalation_log": escalation_log,
    }
