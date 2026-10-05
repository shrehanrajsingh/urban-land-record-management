"""Escalation hierarchy: Helmert → Affine → TPS, with regional validation."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.schema import ParcelVersion
from backend.services.ids import new_id
from geospatial.registration.homologous import extract_homologous_points
from geospatial.registration.ransac import ransac_helmert
from geospatial.registration.transforms import (
    ControlPoint,
    TransformResult,
    apply_matrix,
    apply_tps_geometry,
    compute_residuals,
    estimate_affine,
    estimate_helmert,
    estimate_tps,
    regional_stats,
    residual_heatmap_grid,
    transform_layer,
)

logger = logging.getLogger(__name__)


def _worst_p95(rstats: dict[str, Any]) -> float:
    """Return the worst (maximum) P95 across all regions."""
    if not rstats:
        return 0.0
    return max((v["p95"] for v in rstats.values()), default=0.0)


def _build_region_labels_3x3(src: np.ndarray) -> list[str]:
    """Assign each point to a 3×3 grid region label."""
    if len(src) == 0:
        return []
    xmin, ymin = src.min(axis=0)
    xmax, ymax = src.max(axis=0)
    dx = max((xmax - xmin) / 3.0, 1e-6)
    dy = max((ymax - ymin) / 3.0, 1e-6)
    labels = []
    for x, y in src:
        col = min(int((x - xmin) / dx), 2)
        row = min(int((y - ymin) / dy), 2)
        labels.append(f"r{row}c{col}")
    return labels


def register_with_escalation(
    source_gdf,
    target_gdf,
    uncertainty_envelope_m: float = 0.5,
    control_points: list[dict] | None = None,
) -> TransformResult:
    """Escalation hierarchy: try Helmert → Affine → TPS.

    Parameters
    ----------
    source_gdf : GeoDataFrame with source parcel geometries
    target_gdf : GeoDataFrame with target / reference parcel geometries
    uncertainty_envelope_m : acceptable P95 threshold (metres)
    control_points : optional list of dicts with 'source', 'target', 'region'
        keys. If provided these are used directly instead of auto-extraction.

    Returns
    -------
    TransformResult with the best method chosen.
    """
    escalation_log: list[dict[str, Any]] = []

    # ----- Step 1: obtain correspondences ------------------------------------
    if control_points:
        correspondences = [
            (
                (float(cp["source"][0]), float(cp["source"][1])),
                (float(cp["target"][0]), float(cp["target"][1])),
                cp.get("region", "default"),
            )
            for cp in control_points
        ]
        inlier_mask = [True] * len(correspondences)
    else:
        correspondences = extract_homologous_points(source_gdf, target_gdf)
        if len(correspondences) < 3:
            return TransformResult(
                method="none",
                params={},
                residuals=[],
                global_rmse=float("inf"),
                regional_stats={},
                residual_heatmap={"cells": []},
                passed=False,
                uncertainty_m=float("inf"),
                matrix=None,
            )
        _, inlier_mask, _ = ransac_helmert(correspondences)

    # Build arrays from inlier correspondences
    src_all = np.array([c[0] for c in correspondences], dtype=float)
    dst_all = np.array([c[1] for c in correspondences], dtype=float)
    mask_arr = np.array(inlier_mask, dtype=bool)
    src_in = src_all[mask_arr]
    dst_in = dst_all[mask_arr]

    if len(src_in) < 2:
        return TransformResult(
            method="none",
            params={},
            residuals=[],
            global_rmse=float("inf"),
            regional_stats={},
            residual_heatmap={"cells": []},
            passed=False,
            uncertainty_m=float("inf"),
            matrix=None,
        )

    region_labels = _build_region_labels_3x3(src_in)

    # ----- Step 2: Helmert ---------------------------------------------------
    M_h, params_h = estimate_helmert(src_in, dst_in)
    res_h = compute_residuals(src_in, dst_in, M_h)
    rstats_h = regional_stats(res_h, region_labels)
    heatmap_h = residual_heatmap_grid(src_in, res_h, bins=3)
    worst_h = _worst_p95(rstats_h)
    rmse_h = float(np.sqrt(np.mean(res_h ** 2)))

    escalation_log.append({
        "method": "helmert",
        "global_rmse": rmse_h,
        "worst_p95": worst_h,
        "threshold": uncertainty_envelope_m * 1.5,
        "accepted": worst_h <= uncertainty_envelope_m * 1.5,
    })

    if worst_h <= uncertainty_envelope_m * 1.5:
        logger.info("Helmert accepted (worst P95=%.3f m)", worst_h)
        return TransformResult(
            method="helmert",
            params={**params_h, "escalation_log": escalation_log},
            residuals=res_h.tolist(),
            global_rmse=rmse_h,
            regional_stats=rstats_h,
            residual_heatmap=heatmap_h,
            passed=True,
            uncertainty_m=float(np.percentile(res_h, 95)),
            matrix=M_h,
        )

    # ----- Step 3: Affine ----------------------------------------------------
    if len(src_in) >= 3:
        M_a, params_a = estimate_affine(src_in, dst_in)
        res_a = compute_residuals(src_in, dst_in, M_a)
        rstats_a = regional_stats(res_a, region_labels)
        heatmap_a = residual_heatmap_grid(src_in, res_a, bins=3)
        worst_a = _worst_p95(rstats_a)
        rmse_a = float(np.sqrt(np.mean(res_a ** 2)))

        escalation_log.append({
            "method": "affine",
            "global_rmse": rmse_a,
            "worst_p95": worst_a,
            "threshold": uncertainty_envelope_m * 1.5,
            "accepted": worst_a <= uncertainty_envelope_m * 1.5,
        })

        if worst_a <= uncertainty_envelope_m * 1.5:
            logger.info("Affine accepted (worst P95=%.3f m)", worst_a)
            return TransformResult(
                method="affine",
                params={**params_a, "escalation_log": escalation_log},
                residuals=res_a.tolist(),
                global_rmse=rmse_a,
                regional_stats=rstats_a,
                residual_heatmap=heatmap_a,
                passed=True,
                uncertainty_m=float(np.percentile(res_a, 95)),
                matrix=M_a,
            )
    else:
        escalation_log.append({
            "method": "affine",
            "skipped": True,
            "reason": "insufficient inlier points (< 3)",
        })

    # ----- Step 4: TPS -------------------------------------------------------
    tps_interp = estimate_tps(src_in, dst_in)
    pred_tps = tps_interp(src_in)
    res_tps = np.linalg.norm(pred_tps - dst_in, axis=1)
    rstats_tps = regional_stats(res_tps, region_labels)
    heatmap_tps = residual_heatmap_grid(src_in, res_tps, bins=3)
    worst_tps = _worst_p95(rstats_tps)
    rmse_tps = float(np.sqrt(np.mean(res_tps ** 2)))

    tps_threshold = uncertainty_envelope_m * 2.0
    tps_passed = worst_tps <= tps_threshold

    escalation_log.append({
        "method": "tps",
        "global_rmse": rmse_tps,
        "worst_p95": worst_tps,
        "threshold": tps_threshold,
        "accepted": tps_passed,
    })

    if tps_passed:
        logger.info("TPS accepted (worst P95=%.3f m)", worst_tps)
    else:
        logger.warning("All methods failed — TPS worst P95=%.3f m exceeds %.3f m",
                        worst_tps, tps_threshold)

    # Store the best affine matrix as fallback for geometry application,
    # and attach the TPS interpolator for higher-fidelity transforms
    best_fallback_matrix = None
    if len(src_in) >= 3:
        best_fallback_matrix = M_a  # noqa: F821 — defined in affine block above
    else:
        best_fallback_matrix = M_h

    result = TransformResult(
        method="tps",
        params={"kernel": "thin_plate_spline", "escalation_log": escalation_log},
        residuals=res_tps.tolist(),
        global_rmse=rmse_tps,
        regional_stats=rstats_tps,
        residual_heatmap=heatmap_tps,
        passed=tps_passed,
        uncertainty_m=float(np.percentile(res_tps, 95)),
        matrix=best_fallback_matrix,  # Use best affine as fallback matrix
    )
    # Attach TPS interpolator for high-fidelity geometry transforms
    result._tps_interpolator = tps_interp  # type: ignore[attr-defined]
    return result


def apply_registration_as_new_versions(
    db: Session,
    result: TransformResult,
    source_dataset_id: str,
    transformation_id: str,
) -> int:
    """Create NEW v2 ParcelVersions for each legacy parcel (never overwrite v1).

    Returns the count of new versions created.
    """
    rows = db.execute(
        text(
            """
            SELECT pv.id, pv.parcel_id, pv.version, pv.area,
                   pv.source_ids, ST_AsText(pv.geometry) AS wkt
            FROM parcel_versions pv
            WHERE pv.source_ids @> CAST(:ds AS jsonb)
              AND pv.version = (
                SELECT MAX(pv2.version) FROM parcel_versions pv2
                WHERE pv2.parcel_id = pv.parcel_id
              )
            """
        ),
        {"ds": f'["{source_dataset_id}"]'},
    ).fetchall()

    if not rows:
        return 0

    from shapely import wkt as shapely_wkt

    geoms = [shapely_wkt.loads(r.wkt) for r in rows]

    # Apply the transform — prefer TPS interpolator, fall back to affine matrix
    tps_interp = getattr(result, "_tps_interpolator", None)
    if tps_interp is not None:
        transformed = [apply_tps_geometry(g, tps_interp) for g in geoms]
    elif result.matrix is not None:
        transformed = transform_layer(geoms, result.matrix)
    else:
        logger.warning("No transform available — skipping geometry transform")
        return 0

    count = 0
    for row, new_geom in zip(rows, transformed):
        new_version = row.version + 1
        pv_id = new_id("PV")
        db.add(
            ParcelVersion(
                id=pv_id,
                parcel_id=row.parcel_id,
                version=new_version,
                geometry=WKTElement(new_geom.wkt, srid=32643),
                status="CANDIDATE",
                area=float(new_geom.area) if new_geom.area else row.area,
                attributes={"stage": "registered"},
                source_ids=row.source_ids if row.source_ids else [],
                geometry_uncertainty_m=result.uncertainty_m,
                transformation_id=transformation_id,
                created_by="SYSTEM",
                created_by_role="SYSTEM",
            )
        )
        count += 1

    return count
