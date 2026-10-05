"""TC01, TC05–TC08 conflation / split-merge tests."""

from __future__ import annotations

from shapely.geometry import box

from geospatial.geometry.features import build_pair_features, iou
from geospatial.split_merge.detect import detect_boundary_adjustment, detect_merge, detect_split
from ml.matcher.model import MatchProbabilityModel, generate_synthetic_training_pairs
from ml.matcher.features import FEATURE_COLUMNS


def test_tc01_correct_one_to_one_match():
    a = box(0, 0, 10, 10)
    b = box(0.2, -0.1, 10.2, 9.9)
    feats = build_pair_features(a, b, {"survey_no": "A"}, {"survey_no": "A"})
    assert feats["iou"] > 0.8
    model = MatchProbabilityModel()
    X, y = generate_synthetic_training_pairs(400)
    model.fit(X, y)
    p = float(model.predict_proba(feats)[0])
    assert p > 0.7


def test_tc05_parcel_split():
    parent = box(0, 0, 20, 10)
    c1 = box(0, 0, 10, 10)
    c2 = box(10, 0, 20, 10)
    result = detect_split(parent, [c1, c2])
    assert result.accepted
    assert result.relation == "SPLIT"
    assert result.coverage >= 0.85


def test_tc06_parcel_merge():
    c1 = box(0, 0, 10, 10)
    c2 = box(10, 0, 20, 10)
    parent = box(0, 0, 20, 10)
    result = detect_merge([c1, c2], parent)
    assert result.accepted
    assert result.relation == "MERGE"


def test_tc07_no_match_parcel():
    a = box(0, 0, 10, 10)
    b = box(100, 100, 110, 110)
    feats = build_pair_features(a, b)
    assert feats["iou"] < 0.01
    assert feats["centroid_distance"] > 50


def test_tc08_overlapping_candidates_ranked():
    src = box(0, 0, 10, 10)
    good = box(0.5, 0.5, 10.5, 10.5)
    bad = box(8, 8, 18, 18)
    assert iou(src, good) > iou(src, bad)


def test_boundary_adjustment():
    a = box(0, 0, 10, 10)
    b = box(0.3, 0.2, 10.4, 10.1)
    r = detect_boundary_adjustment(a, b)
    assert r.accepted
