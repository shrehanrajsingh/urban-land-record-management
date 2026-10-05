"""TC15 — AI cannot publish authoritative records."""

from __future__ import annotations

import os

import pytest
from shapely.geometry import box

from backend.services.review.workflow import ALLOWED_AI_STATUSES, create_candidate_version


def test_ai_allowed_statuses_do_not_include_published():
    assert "PUBLISHED" not in ALLOWED_AI_STATUSES
    assert "APPROVED" not in ALLOWED_AI_STATUSES


@pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS", "0") != "1",
    reason="Set RUN_DB_TESTS=1 with PostGIS available",
)
def test_tc15_ai_publish_rejected():
    from backend.db import SessionLocal
    from backend.models.schema import Parcel
    from backend.services.ids import new_id
    from geoalchemy2.elements import WKTElement

    db = SessionLocal()
    try:
        pid = new_id("P")
        db.add(Parcel(id=pid, external_id="tc15"))
        db.commit()
        with pytest.raises(PermissionError):
            create_candidate_version(
                db,
                parcel_id=pid,
                geometry_wkt=box(0, 0, 1, 1).wkt,
                status="PUBLISHED",
                attributes={},
                source_ids=["X"],
                match_probability=0.99,
                geometry_uncertainty_m=0.1,
                created_by="ai",
                created_by_role="AI_SERVICE",
            )
    finally:
        db.close()
