"""RANSAC wrapper for robust Helmert estimation."""

from __future__ import annotations

import numpy as np

from geospatial.registration.transforms import compute_residuals, estimate_helmert


def ransac_helmert(
    correspondences: list[tuple[tuple[float, float], tuple[float, float], str]],
    inlier_threshold: float = 2.0,
    min_samples: int = 3,
    max_iterations: int = 500,
    seed: int = 42,
) -> tuple[np.ndarray, list[bool], dict]:
    """RANSAC-based robust Helmert estimation.

    Parameters
    ----------
    correspondences : list of (source_xy, target_xy, region)
    inlier_threshold : maximum residual (metres) to be counted as inlier
    min_samples : points drawn per iteration (>= 2 for Helmert)
    max_iterations : RANSAC iterations
    seed : random seed for reproducibility

    Returns
    -------
    (3×3 matrix, inlier_mask, params_dict)
    """
    if len(correspondences) < min_samples:
        raise ValueError(
            f"Need at least {min_samples} correspondences, got {len(correspondences)}"
        )

    src_all = np.array([c[0] for c in correspondences], dtype=float)
    dst_all = np.array([c[1] for c in correspondences], dtype=float)

    rng = np.random.default_rng(seed)
    n = len(correspondences)

    best_inlier_count = -1
    best_mask: np.ndarray = np.zeros(n, dtype=bool)
    best_matrix: np.ndarray = np.eye(3)
    best_params: dict = {}

    for _ in range(max_iterations):
        # 1. Random sample
        idx = rng.choice(n, size=min_samples, replace=False)
        src_sample = src_all[idx]
        dst_sample = dst_all[idx]

        # 2. Fit Helmert on the sample
        try:
            M, params = estimate_helmert(src_sample, dst_sample)
        except Exception:
            continue

        # 3. Compute residuals for all points
        residuals = compute_residuals(src_all, dst_all, M)

        # 4. Count inliers
        mask = residuals < inlier_threshold
        count = int(mask.sum())

        if count > best_inlier_count:
            best_inlier_count = count
            best_mask = mask
            best_matrix = M
            best_params = params

    # 5. Re-fit on all inliers
    if best_inlier_count >= 2:
        src_inliers = src_all[best_mask]
        dst_inliers = dst_all[best_mask]
        try:
            best_matrix, best_params = estimate_helmert(src_inliers, dst_inliers)
            # Recompute mask after refit
            residuals = compute_residuals(src_all, dst_all, best_matrix)
            best_mask = residuals < inlier_threshold
        except Exception:
            pass

    inlier_mask = best_mask.tolist()

    best_params["inlier_count"] = int(best_mask.sum())
    best_params["total_points"] = n
    best_params["inlier_ratio"] = float(best_mask.sum() / n) if n > 0 else 0.0

    return best_matrix, inlier_mask, best_params
