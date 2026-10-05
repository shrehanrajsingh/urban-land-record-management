"""Tests for registration escalation and RANSAC."""

from __future__ import annotations

import numpy as np

from geospatial.registration.ransac import ransac_helmert
from geospatial.registration.transforms import (
    apply_matrix_coords,
    estimate_helmert,
)


def test_ransac_with_clean_data():
    """RANSAC on clean data should produce perfect fit."""
    src = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [5, 5]], dtype=float)
    dst = src + np.array([2.0, -1.0])
    correspondences = [(tuple(s), tuple(d), "NW") for s, d in zip(src, dst)]
    M, inliers, params = ransac_helmert(correspondences, inlier_threshold=0.5)
    assert all(inliers), "All points should be inliers for clean translation"
    pred = apply_matrix_coords(src, M)
    assert np.allclose(pred, dst, atol=0.01)


def test_ransac_rejects_outliers():
    """RANSAC should reject outlier correspondences."""
    rng = np.random.default_rng(42)
    src = rng.uniform(0, 100, size=(20, 2))
    # True transform: translate (3, -2)
    dst = src + np.array([3.0, -2.0])
    # Add 3 outliers with large displacement
    outlier_idx = [0, 5, 10]
    for i in outlier_idx:
        dst[i] += rng.uniform(20, 50, size=2)

    correspondences = [(tuple(s), tuple(d), "NW") for s, d in zip(src, dst)]
    M, inliers, params = ransac_helmert(correspondences, inlier_threshold=2.0)

    inlier_count = sum(inliers)
    assert inlier_count >= 15, f"Expected most points as inliers, got {inlier_count}"

    # Inlier residuals should be small
    inlier_src = src[inliers]
    inlier_dst = dst[inliers]
    pred = apply_matrix_coords(inlier_src, M)
    residuals = np.linalg.norm(pred - inlier_dst, axis=1)
    assert residuals.max() < 2.0


def test_helmert_recovery_with_rotation():
    """Helmert should recover rotation + translation."""
    ang = np.deg2rad(2.0)
    R = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    src = np.array([[0, 0], [100, 0], [100, 100], [0, 100], [50, 50]], dtype=float)
    dst = (R @ src.T).T + np.array([5.0, -3.0])
    M, params = estimate_helmert(src, dst)
    assert abs(params["rotation_deg"] - 2.0) < 0.1
    pred = apply_matrix_coords(src, M)
    assert np.linalg.norm(pred - dst, axis=1).max() < 0.01
