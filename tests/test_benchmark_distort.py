"""Synthetic benchmark generator smoke tests."""

from __future__ import annotations

from benchmark.distortions.distort import DistortionSpec, distort
from benchmark.truth.generate import generate_control_points, generate_grid_fabric


def test_truth_and_distort_pipeline():
    truth = generate_grid_fabric(rows=3, cols=3, seed=1)
    assert len(truth) >= 9
    legacy, params = distort(
        truth,
        DistortionSpec(translation=(1.0, -0.5), rotation_deg=0.5, scale=1.001, vertex_noise=0.1, seed=1),
    )
    assert len(legacy) == len(truth)
    assert params["translation"] == (1.0, -0.5)
    cps = generate_control_points(truth, n=6)
    assert len(cps) == 6


def test_scenario_generator():
    """Smoke test for the new scenario generator."""
    from benchmark.scenario import generate_scenario

    s = generate_scenario(seed=99)
    assert len(s["truth"]) > 0
    assert len(s["legacy"]) > 0
    assert len(s["survey"]) > 0
    assert len(s["control_points"]) > 0
