"""TC02–TC04 registration tests."""

from __future__ import annotations

import numpy as np

from geospatial.registration.transforms import (
    ControlPoint,
    apply_matrix_coords,
    estimate_affine,
    estimate_helmert,
    register_control_points,
    transform_layer,
)
from shapely.geometry import box


def test_tc02_shifted_legacy_map():
    src = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
    dst = src + np.array([1.2, -0.7])
    M, params = estimate_helmert(src, dst)
    pred = apply_matrix_coords(src, M)
    err = np.linalg.norm(pred - dst, axis=1).max()
    assert err < 1e-6
    assert abs(params["tx"] - 1.2) < 1e-6
    assert abs(params["ty"] + 0.7) < 1e-6


def test_tc03_rotated_legacy_map():
    src = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
    ang = np.deg2rad(0.8)
    R = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    dst = (R @ src.T).T
    M, params = estimate_helmert(src, dst)
    assert abs(params["rotation_deg"] - 0.8) < 0.05
    pred = apply_matrix_coords(src, M)
    assert np.linalg.norm(pred - dst, axis=1).max() < 1e-5


def test_tc04_locally_distorted_sheet_regional_stats():
    rng = np.random.default_rng(0)
    src = rng.uniform(0, 100, size=(20, 2))
    dst = src + np.array([0.3, -0.2])
    # add local distortion in SE region
    for i, p in enumerate(src):
        if p[0] > 50 and p[1] < 50:
            dst[i] += rng.normal(0, 0.05, size=2)
    points = [
        ControlPoint(tuple(s), tuple(d), region=("SE" if s[0] > 50 and s[1] < 50 else "NW"))
        for s, d in zip(src, dst)
    ]
    result = register_control_points(points, method="affine", uncertainty_envelope_m=0.5)
    assert result.global_rmse < 0.3
    assert "regional_stats" in result.__dict__
    assert result.passed


def test_whole_layer_transform_preserves_relative_topology():
    geoms = [box(0, 0, 1, 1), box(1, 0, 2, 1)]
    M = np.array([[1, 0, 2], [0, 1, -1], [0, 0, 1]], dtype=float)
    out = transform_layer(geoms, M)
    gap = out[0].distance(out[1])
    assert gap < 1e-9
