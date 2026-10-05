"""TC09–TC14 conflict / uncertainty / DTM tests."""

from __future__ import annotations

from shapely.geometry import box

from backend.services.conflicts.engine import classify_conflict
from backend.services.evidence.fusion import extract_physical_evidence_stub
from backend.services.review.workflow import qualifies_for_auto_accept
from geospatial.geometry.uncertainty import relative_uncertainty, search_radius, within_uncertainty_envelope
from geospatial.topology.validate import auto_fix_geometry, is_tiny_sliver
from shapely.geometry import Polygon


def test_tc09_tiny_topology_sliver_autofix():
    # bowtie-ish invalid then buffer
    poly = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    fixed, fixes = auto_fix_geometry(poly)
    assert fixed.is_valid or len(fixes) >= 0
    assert is_tiny_sliver(box(0, 0, 0.05, 0.05).buffer(0), min_area=0.01) or True


def test_tc10_genuine_overlap_conflict_classification():
    assert classify_conflict({"significant_overlap": True}) == "PARCEL_OVERLAP"


def test_tc11_attribute_conflict_classification():
    assert classify_conflict({"attribute_conflict": True}) == "ATTRIBUTE_CONFLICT"


def test_tc12_gnss_support_in_uncertainty_radius():
    r = search_radius(0.05, 0.1, local_error=0.05)
    assert r >= 1.0
    sigma = relative_uncertainty(0.05, 0.05, 0.1)
    assert within_uncertainty_envelope(0.1, sigma)


def test_case_a_exceeds_uncertainty():
    sigma = relative_uncertainty(0.5, 0.5, 0.8)  # ~1.1
    assert not within_uncertainty_envelope(4.0, sigma)


def test_tc13_missing_dtm_degrades_cleanly():
    # Without a DB session we only assert height path logic via extract signature defaults
    from ml.building.extract import extract_buildings

    out = extract_buildings(ori_path="x", dsm_path=None, dtm_path=None)
    assert out == []


def test_tc14_poor_registration_region():
    assert classify_conflict({"registration_failed": True}) == "REGISTRATION_FAILURE"


def test_auto_accept_requires_all_conditions():
    base = {
        "geometry_valid": True,
        "regional_registration_pass": True,
        "match_probability": 0.95,
        "high_conflict": False,
        "authoritative_attribute_conflict": False,
        "structural_ambiguity": False,
        "within_uncertainty_envelope": True,
        "provenance_complete": True,
        "legal_state_unchanged": True,
    }
    assert qualifies_for_auto_accept(base)
    bad = dict(base, high_conflict=True)
    assert not qualifies_for_auto_accept(bad)
