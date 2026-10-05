"""Review workflow, auto-accept, versioning, provenance, audit."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from geoalchemy2.elements import WKTElement
from shapely import wkt as shapely_wkt
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db import set_app_role
from backend.models.enums import ActorRole, ParcelStatus
from backend.models.schema import (
    AuditEvent,
    ParcelVersion,
    Provenance,
    ReviewAction,
    ReviewCase,
)
from backend.services.ids import new_id

settings = get_settings()


# AI may auto-accept into VALIDATED; APPROVED/PUBLISHED remain human-only (DB trigger + app).
ALLOWED_AI_STATUSES = {
    ParcelStatus.OBSERVED.value,
    ParcelStatus.CANDIDATE.value,
    ParcelStatus.VALIDATED.value,
    ParcelStatus.REVIEW.value,
    ParcelStatus.FIELD_VERIFICATION.value,
    ParcelStatus.REJECTED.value,
}

TRANSITIONS = {
    "OBSERVED": {"CANDIDATE", "REVIEW", "REJECTED"},
    "CANDIDATE": {"VALIDATED", "REVIEW", "FIELD_VERIFICATION", "REJECTED"},
    "VALIDATED": {"APPROVED", "REVIEW", "REJECTED"},
    "REVIEW": {"CANDIDATE", "VALIDATED", "APPROVED", "REJECTED", "FIELD_VERIFICATION"},
    "FIELD_VERIFICATION": {"APPROVED", "REJECTED", "REVIEW"},
    "APPROVED": {"PUBLISHED", "REVIEW"},
    "REJECTED": set(),
    "PUBLISHED": set(),
}


def qualifies_for_auto_accept(candidate: dict[str, Any]) -> bool:
    return all(
        [
            candidate.get("geometry_valid", True),
            candidate.get("regional_registration_pass", False),
            float(candidate.get("match_probability", 0)) >= settings.auto_match_threshold,
            not candidate.get("high_conflict", True),
            not candidate.get("authoritative_attribute_conflict", True),
            not candidate.get("structural_ambiguity", True),
            candidate.get("within_uncertainty_envelope", False),
            candidate.get("provenance_complete", False),
            candidate.get("legal_state_unchanged", False),
        ]
    )


def create_candidate_version(
    db: Session,
    *,
    parcel_id: str,
    geometry_wkt: str,
    status: str,
    attributes: dict[str, Any],
    source_ids: list[str],
    match_probability: float | None,
    geometry_uncertainty_m: float | None,
    created_by: str,
    created_by_role: str,
    transformation_id: str | None = None,
    model_run_id: str | None = None,
    review_case_id: str | None = None,
    lineage: list[Any] | None = None,
) -> str:
    set_app_role(db, created_by_role)
    if created_by_role == ActorRole.AI_SERVICE.value and status not in ALLOWED_AI_STATUSES:
        raise PermissionError(f"AI_SERVICE cannot create status {status}")

    row = db.execute(
        text("SELECT COALESCE(MAX(version), 0) AS v FROM parcel_versions WHERE parcel_id = :p"),
        {"p": parcel_id},
    ).mappings().first()
    next_v = int(row["v"]) + 1
    pv_id = new_id("PV")
    geom = shapely_wkt.loads(geometry_wkt)
    db.add(
        ParcelVersion(
            id=pv_id,
            parcel_id=parcel_id,
            version=next_v,
            geometry=WKTElement(geom.wkt, srid=32643),
            status=status,
            area=float(geom.area),
            attributes=attributes,
            source_ids=source_ids,
            match_probability=match_probability,
            geometry_uncertainty_m=geometry_uncertainty_m,
            valid_from=datetime.now(timezone.utc),
            created_by=created_by,
            created_by_role=created_by_role,
            review_case_id=review_case_id,
            transformation_id=transformation_id,
            model_run_id=model_run_id,
        )
    )
    db.add(
        Provenance(
            id=new_id("PRV"),
            parcel_version_id=pv_id,
            lineage=lineage or [],
            derived_from=source_ids,
            registration_run_id=transformation_id,
            match_model=model_run_id,
            review_case_id=review_case_id,
            details={"status": status},
        )
    )
    db.add(
        AuditEvent(
            id=new_id("AUD"),
            event_type="PARCEL_VERSION_CREATED",
            actor=created_by,
            actor_role=created_by_role,
            entity_type="parcel_version",
            entity_id=pv_id,
            details={"parcel_id": parcel_id, "version": next_v, "status": status},
        )
    )
    db.commit()
    return pv_id


def maybe_audit_auto_accept(db: Session, parcel_version_id: str) -> bool:
    if random.random() < settings.audit_sample_rate:
        db.add(
            AuditEvent(
                id=new_id("AUD"),
                event_type="AUTO_ACCEPT_AUDIT_SAMPLE",
                actor="SYSTEM",
                actor_role="SYSTEM",
                entity_type="parcel_version",
                entity_id=parcel_version_id,
                details={"sample_rate": settings.audit_sample_rate},
            )
        )
        # Also open a light review case for the audit sample
        db.add(
            ReviewCase(
                id=new_id("RC"),
                parcel_version_id=parcel_version_id,
                status="OPEN",
                summary="Random audit sample of AUTO-ACCEPT",
                evidence_snapshot={"reason": "audit_sample"},
            )
        )
        db.commit()
        return True
    return False


def apply_review_action(
    db: Session,
    *,
    review_case_id: str,
    action: str,
    actor: str,
    actor_role: str,
    notes: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    set_app_role(db, actor_role)
    case = db.get(ReviewCase, review_case_id)
    if case is None:
        raise ValueError("review case not found")

    action_id = new_id("RA")
    db.add(
        ReviewAction(
            id=action_id,
            review_case_id=review_case_id,
            action=action,
            actor=actor,
            actor_role=actor_role,
            notes=notes,
            payload=payload or {},
        )
    )

    status_map = {
        "APPROVE": ParcelStatus.APPROVED.value,
        "REJECT": ParcelStatus.REJECTED.value,
        "FIELD_VERIFY": ParcelStatus.FIELD_VERIFICATION.value,
        "EDIT": ParcelStatus.CANDIDATE.value,
    }
    new_status = status_map.get(action)
    new_version_id = None

    if new_status and case.candidate_match_id:
        # Create new version reflecting human decision
        match_row = db.execute(
            text("SELECT source_parcel_id, target_parcel_ids, match_probability, evidence FROM candidate_matches WHERE id = :id"),
            {"id": case.candidate_match_id},
        ).mappings().first()
        if match_row:
            target_ids = match_row["target_parcel_ids"] or [match_row["source_parcel_id"]]
            pid = target_ids[0] if target_ids else match_row["source_parcel_id"]
            geom_row = db.execute(
                text(
                    """
                    SELECT ST_AsText(geometry) AS wkt, attributes, source_ids, geometry_uncertainty_m
                    FROM parcel_versions
                    WHERE parcel_id = :p
                    ORDER BY version DESC LIMIT 1
                    """
                ),
                {"p": pid},
            ).mappings().first()
            if geom_row:
                attrs = dict(geom_row["attributes"] or {})
                if action == "EDIT" and payload and "attributes" in payload:
                    attrs.update(payload["attributes"])
                if action == "EDIT" and payload and "geometry_wkt" in payload:
                    wkt = payload["geometry_wkt"]
                else:
                    wkt = geom_row["wkt"]
                new_version_id = create_candidate_version(
                    db,
                    parcel_id=pid,
                    geometry_wkt=wkt,
                    status=new_status,
                    attributes=attrs,
                    source_ids=list(geom_row["source_ids"] or []),
                    match_probability=float(match_row["match_probability"]),
                    geometry_uncertainty_m=float(geom_row["geometry_uncertainty_m"] or 0.25),
                    created_by=actor,
                    created_by_role=actor_role,
                    review_case_id=review_case_id,
                    lineage=[
                        {"step": "review_action", "action": action, "case": review_case_id},
                        {"step": "candidate_match", "id": case.candidate_match_id},
                    ],
                )

    case.status = "CLOSED" if action in ("APPROVE", "REJECT") else "IN_PROGRESS"
    if case.conflict_id and action == "APPROVE":
        db.execute(text("UPDATE conflicts SET resolved = true WHERE id = :id"), {"id": case.conflict_id})

    db.add(
        AuditEvent(
            id=new_id("AUD"),
            event_type="REVIEW_ACTION",
            actor=actor,
            actor_role=actor_role,
            entity_type="review_case",
            entity_id=review_case_id,
            details={"action": action, "new_version_id": new_version_id},
        )
    )
    db.commit()
    return {
        "action_id": action_id,
        "review_case_id": review_case_id,
        "new_status": new_status,
        "parcel_version_id": new_version_id,
        "case_status": case.status,
    }
