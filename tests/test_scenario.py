"""Tests for the deterministic scenario generator."""

from __future__ import annotations

from benchmark.scenario import generate_scenario


def test_scenario_produces_all_layers():
    s = generate_scenario(seed=42)
    assert "truth" in s
    assert "survey" in s
    assert "legacy" in s
    assert "gnss" in s
    assert "ground_truth" in s
    assert "control_points" in s


def test_truth_has_expected_count():
    s = generate_scenario(seed=42)
    # 6x5 = 30 parcels
    assert len(s["truth"]) >= 25


def test_survey_has_splits_and_merges():
    s = generate_scenario(seed=42)
    survey = s["survey"]
    ids = set(survey["parcel_id"])
    # Split: P-104 -> P-202, P-203
    assert "P-202" in ids or "S-P-202" in ids or any("202" in x for x in ids)
    # Merge: P-020 + P-021 -> P-230
    assert "P-230" in ids or "S-P-230" in ids or any("230" in x for x in ids)


def test_legacy_has_distortion():
    s = generate_scenario(seed=42)
    truth = s["truth"]
    legacy = s["legacy"]
    # Legacy should have different centroid than truth (due to translation + rotation)
    tc = truth.geometry.centroid.unary_union.centroid
    lc = legacy.geometry.centroid.unary_union.centroid
    dist = tc.distance(lc)
    assert dist > 0.5, "Legacy should be displaced from truth"


def test_ground_truth_has_correspondences():
    s = generate_scenario(seed=42)
    gt = s["ground_truth"]
    assert "correspondences" in gt
    assert len(gt["correspondences"]) > 0
    # Check relation types present
    types = {c["relation_type"] for c in gt["correspondences"]}
    assert "ONE_TO_ONE" in types


def test_ground_truth_has_injected_conflicts():
    s = generate_scenario(seed=42)
    gt = s["ground_truth"]
    assert "injected_conflicts" in gt
    assert len(gt["injected_conflicts"]) > 0


def test_gnss_points_generated():
    s = generate_scenario(seed=42)
    gnss = s["gnss"]
    assert len(gnss) > 0
    assert "uncertainty_m" in gnss.columns


def test_control_points_generated():
    s = generate_scenario(seed=42)
    cps = s["control_points"]
    assert len(cps) > 0
    assert "source" in cps[0]
    assert "target" in cps[0]


def test_deterministic():
    s1 = generate_scenario(seed=42)
    s2 = generate_scenario(seed=42)
    assert len(s1["truth"]) == len(s2["truth"])
    assert len(s1["legacy"]) == len(s2["legacy"])
