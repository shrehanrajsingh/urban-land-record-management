"""Whole-layer registration: Helmert / affine / optional TPS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.interpolate import RBFInterpolator
from shapely.affinity import affine_transform
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform


@dataclass
class TransformResult:
    method: str
    params: dict[str, Any]
    residuals: list[float]
    global_rmse: float
    regional_stats: dict[str, Any]
    residual_heatmap: dict[str, Any]
    passed: bool
    uncertainty_m: float
    matrix: np.ndarray | None = None


@dataclass
class ControlPoint:
    source: tuple[float, float]
    target: tuple[float, float]
    region: str = "default"


def estimate_helmert(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Similarity transform: scale, rotation, translation. Returns 3x3 matrix."""
    assert src.shape == dst.shape and src.shape[0] >= 2
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    src_z = src - src_c
    dst_z = dst - dst_c
    norm_s = np.linalg.norm(src_z)
    norm_d = np.linalg.norm(dst_z)
    scale = (norm_d / norm_s) if norm_s > 0 else 1.0
    # complex multiplication for 2D rotation
    a = src_z[:, 0] + 1j * src_z[:, 1]
    b = dst_z[:, 0] + 1j * dst_z[:, 1]
    rot = np.angle((b / (a + 1e-12)).sum())
    c, s = np.cos(rot), np.sin(rot)
    R = scale * np.array([[c, -s], [s, c]])
    t = dst_c - R @ src_c
    M = np.eye(3)
    M[:2, :2] = R
    M[:2, 2] = t
    params = {
        "scale": float(scale),
        "rotation_deg": float(np.degrees(rot)),
        "tx": float(t[0]),
        "ty": float(t[1]),
    }
    return M, params


def estimate_affine(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Full affine via least squares. Needs >= 3 points."""
    n = src.shape[0]
    A = np.zeros((2 * n, 6))
    b = np.zeros(2 * n)
    for i, (x, y) in enumerate(src):
        A[2 * i] = [x, y, 1, 0, 0, 0]
        A[2 * i + 1] = [0, 0, 0, x, y, 1]
        b[2 * i] = dst[i, 0]
        b[2 * i + 1] = dst[i, 1]
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    a, b_, c, d, e, f = coef
    M = np.array([[a, b_, c], [d, e, f], [0, 0, 1]], dtype=float)
    params = {"a": float(a), "b": float(b_), "c": float(c), "d": float(d), "e": float(e), "f": float(f)}
    return M, params


def apply_matrix(geom: BaseGeometry, M: np.ndarray) -> BaseGeometry:
    # shapely affine_transform uses [a, b, d, e, xoff, yoff]
    return affine_transform(geom, [M[0, 0], M[0, 1], M[1, 0], M[1, 1], M[0, 2], M[1, 2]])


def apply_matrix_coords(coords: np.ndarray, M: np.ndarray) -> np.ndarray:
    ones = np.ones((coords.shape[0], 1))
    homo = np.hstack([coords, ones])
    out = (M @ homo.T).T
    return out[:, :2]


def compute_residuals(src: np.ndarray, dst: np.ndarray, M: np.ndarray) -> np.ndarray:
    pred = apply_matrix_coords(src, M)
    return np.linalg.norm(pred - dst, axis=1)


def regional_stats(
    residuals: np.ndarray,
    regions: list[str],
) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for region in sorted(set(regions)):
        mask = np.array([r == region for r in regions])
        vals = residuals[mask]
        if len(vals) == 0:
            continue
        rmse = float(np.sqrt(np.mean(vals**2)))
        stats[region] = {
            "n": int(len(vals)),
            "rmse": rmse,
            "p50": float(np.percentile(vals, 50)),
            "p95": float(np.percentile(vals, 95)),
            "max": float(np.max(vals)),
        }
    return stats


def residual_heatmap_grid(
    src: np.ndarray,
    residuals: np.ndarray,
    bins: int = 4,
) -> dict[str, Any]:
    if len(src) == 0:
        return {"cells": []}
    xmin, ymin = src.min(axis=0)
    xmax, ymax = src.max(axis=0)
    dx = max((xmax - xmin) / bins, 1e-6)
    dy = max((ymax - ymin) / bins, 1e-6)
    cells = []
    for i in range(bins):
        for j in range(bins):
            x0, x1 = xmin + i * dx, xmin + (i + 1) * dx
            y0, y1 = ymin + j * dy, ymin + (j + 1) * dy
            mask = (
                (src[:, 0] >= x0)
                & (src[:, 0] < x1 + (1e-9 if i == bins - 1 else 0))
                & (src[:, 1] >= y0)
                & (src[:, 1] < y1 + (1e-9 if j == bins - 1 else 0))
            )
            vals = residuals[mask]
            cells.append(
                {
                    "i": i,
                    "j": j,
                    "bounds": [float(x0), float(y0), float(x1), float(y1)],
                    "n": int(mask.sum()),
                    "mean_residual": float(vals.mean()) if len(vals) else None,
                    "p95": float(np.percentile(vals, 95)) if len(vals) else None,
                }
            )
    return {"bins": bins, "cells": cells}


def register_control_points(
    points: list[ControlPoint],
    method: str = "auto",
    uncertainty_envelope_m: float = 0.5,
    p95_threshold: float | None = None,
) -> TransformResult:
    src = np.array([p.source for p in points], dtype=float)
    dst = np.array([p.target for p in points], dtype=float)
    regions = [p.region for p in points]

    chosen = method
    if method == "auto":
        chosen = "affine" if len(points) >= 3 else "helmert"

    if chosen == "helmert":
        M, params = estimate_helmert(src, dst)
    elif chosen == "affine":
        M, params = estimate_affine(src, dst)
    else:
        raise ValueError(f"Unknown method: {chosen}")

    residuals = compute_residuals(src, dst, M)
    rmse = float(np.sqrt(np.mean(residuals**2)))
    reg = regional_stats(residuals, regions)
    heatmap = residual_heatmap_grid(src, residuals)

    threshold = p95_threshold if p95_threshold is not None else uncertainty_envelope_m
    regional_ok = all(s["p95"] <= threshold * 1.5 for s in reg.values()) if reg else True
    global_ok = float(np.percentile(residuals, 95)) <= threshold
    passed = regional_ok and global_ok

    uncertainty = float(np.percentile(residuals, 95)) if len(residuals) else uncertainty_envelope_m

    return TransformResult(
        method=chosen,
        params=params,
        residuals=[float(r) for r in residuals],
        global_rmse=rmse,
        regional_stats=reg,
        residual_heatmap=heatmap,
        passed=passed,
        uncertainty_m=uncertainty,
        matrix=M,
    )


def estimate_tps(
    src: np.ndarray,
    dst: np.ndarray,
) -> RBFInterpolator:
    """Thin-plate spline for local distortion (level 3)."""
    return RBFInterpolator(src, dst, kernel="thin_plate_spline", smoothing=0.1)


def apply_tps_geometry(geom: BaseGeometry, interpolator: RBFInterpolator) -> BaseGeometry:
    def _f(x, y, z=None):
        pts = np.column_stack([x, y])
        out = interpolator(pts)
        if z is None:
            return out[:, 0], out[:, 1]
        return out[:, 0], out[:, 1], z

    return shapely_transform(_f, geom)


def transform_layer(geometries: list[BaseGeometry], M: np.ndarray) -> list[BaseGeometry]:
    """Apply the same transform to an entire sheet/block — never per-parcel independent transforms."""
    return [apply_matrix(g, M) for g in geometries]
