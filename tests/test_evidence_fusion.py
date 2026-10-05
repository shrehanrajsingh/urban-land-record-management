"""Tests for six-dimension evidence fusion."""

from __future__ import annotations

from backend.services.evidence.fusion import fuse_evidence, EVIDENCE_DIMENSIONS


def test_fuse_evidence_returns_all_six_dimensions():
    match = {
        "probability": 0.85,
        "evidence": {
            "geometry_compatibility": 0.9,
            "attribute_compatibility": 0.7,
            "survey_support": 0.6,
            "physical_support": 0.5,
            "temporal_support": 0.8,
        },
    }
    result = fuse_evidence(match)
    for dim in EVIDENCE_DIMENSIONS:
        assert dim in result, f"Missing dimension: {dim}"


def test_fuse_evidence_computes_fusion_score():
    match = {
        "probability": 0.9,
        "evidence": {
            "geometry_compatibility": 1.0,
            "attribute_compatibility": 1.0,
            "survey_support": 1.0,
            "physical_support": 1.0,
            "temporal_support": 1.0,
            "identity_compatibility": 1.0,
        },
    }
    result = fuse_evidence(match)
    assert result["fusion_score"] > 0.9
    assert not result["disagreement"]


def test_fuse_evidence_detects_disagreement():
    match = {
        "probability": 0.5,
        "evidence": {
            "geometry_compatibility": 0.9,
            "attribute_compatibility": 0.1,
            "survey_support": 0.8,
            "physical_support": 0.9,
            "temporal_support": 0.2,
            "identity_compatibility": 0.1,
        },
    }
    result = fuse_evidence(match)
    assert result["disagreement"], "High geometry + low attribute should be disagreement"


def test_fuse_evidence_preserves_match_probability():
    match = {"probability": 0.42, "evidence": {}}
    result = fuse_evidence(match)
    assert result["match_probability"] == 0.42
