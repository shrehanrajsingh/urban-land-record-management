"""Tests for benchmark evaluation metrics."""

from __future__ import annotations

from benchmark.evaluation.metrics import (
    conflict_detection_metrics,
    conflation_precision,
    relation_accuracy,
    registration_quality,
)


def test_conflation_precision_perfect():
    assert conflation_precision(10, 10) == 1.0


def test_conflation_precision_zero():
    assert conflation_precision(0, 10) == 0.0


def test_conflation_precision_empty():
    assert conflation_precision(0, 0) == 0.0


def test_relation_accuracy_perfect():
    predicted = [
        {"source_parcel_id": "L-P-001", "target_parcel_ids": ["S-001"], "relation": "ONE_TO_ONE"},
        {"source_parcel_id": "L-P-002", "target_parcel_ids": ["S-002"], "relation": "ONE_TO_ONE"},
    ]
    gt = [
        {"legacy_id": "L-P-001", "reference_ids": ["S-001"], "relation_type": "ONE_TO_ONE"},
        {"legacy_id": "L-P-002", "reference_ids": ["S-002"], "relation_type": "ONE_TO_ONE"},
    ]
    result = relation_accuracy(predicted, gt)
    assert result["overall_accuracy"] == 1.0


def test_relation_accuracy_wrong_type():
    predicted = [
        {"source_parcel_id": "L-P-001", "target_parcel_ids": ["S-001"], "relation": "SPLIT"},
    ]
    gt = [
        {"legacy_id": "L-P-001", "reference_ids": ["S-001"], "relation_type": "ONE_TO_ONE"},
    ]
    result = relation_accuracy(predicted, gt)
    assert result["overall_accuracy"] == 0.0


def test_conflict_detection_recall():
    detected = [{"parcel_ids": ["P-001", "P-002"]}, {"parcel_ids": ["P-003"]}]
    injected = [
        {"parcel_id": "P-001", "conflict_type": "OVERLAP"},
        {"parcel_id": "P-003", "conflict_type": "ATTR"},
    ]
    result = conflict_detection_metrics(detected, injected)
    assert result["recall"] >= 0.5  # P-001 and P-003 both detected


def test_registration_quality_pass():
    stats = {
        "NW": {"n": 5, "rmse": 0.2, "p50": 0.15, "p95": 0.35, "max": 0.4},
        "SE": {"n": 5, "rmse": 0.3, "p50": 0.25, "p95": 0.6, "max": 0.7},
    }
    result = registration_quality(stats, uncertainty_threshold=0.5)
    assert result["pass"]


def test_registration_quality_fail():
    stats = {
        "NW": {"n": 5, "rmse": 0.2, "p50": 0.15, "p95": 0.35, "max": 0.4},
        "SE": {"n": 5, "rmse": 1.5, "p50": 1.2, "p95": 2.5, "max": 3.0},
    }
    result = registration_quality(stats, uncertainty_threshold=0.5)
    assert not result["pass"]
