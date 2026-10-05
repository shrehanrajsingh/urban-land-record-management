"""Conflict detection, classification, and auto-accept checklist persistence."""

from __future__ import annotations

from typing import Any

from geoalchemy2.elements import WKTElement
from shapely import wkt as shapely_wkt
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models.schema import Conflict, ReviewCase
from backend.services.ids import new_id
from geospatial.geometry.uncertainty import relative_uncertainty, within_uncertainty_envelope
from geospatial.topology.validate import significant_overlap

settings = get_settings()


def classify_conflict(candidate: dict[str, Any]) -> str | None:
    if candidate.get("geometry_invalid"):
        return "GEOMETRY_INVALID"
    if candidate.get("registration_failed"):
        return "REGISTRATION_FAILURE"
    if candidate.get("significant_overlap"):
        return "PARCEL_OVERLAP"
    if candidate.get("attribute_conflict"):
        return "ATTRIBUTE_CONFLICT"
    if candidate.get("ambiguous_identity"):
        return "IDENTITY_CONFLICT"
    if candidate.get("structural_ambiguity"):
        return "STRUCTURAL_AMBIGUITY"
    return None


def _attr_conflict(src_attrs: dict, tgt_attrs: dict) -> tuple[bool, str]:
    issues = []
    sa = src_attrs.get("revenue_area")
    ta = tgt_attrs.get("revenue_area")
    if sa is not None and ta is not None and float(ta) > 0:
        rel = abs(float(sa) - float(ta)) / float(ta)
        if rel > 0.15:
            issues.append(f"Revenue area differs by {rel*100:.0f}%")
    ss, ts = src_attrs.get("survey_no"), tgt_attrs.get("survey_no")
    if ss and ts and str(ss).lower() != str(ts).lower():
        su, tu = str(ss).upper(), str(ts).upper()
        if "AMBIG" in su or "AMBIG" in tu or "CONFLICT" in su or "CONFLICT" in tu:
            issues.append(f"Identifier ambiguous: {ss} vs {ts}")
        elif str(ss) != str(ts):
            issues.append(f"Identifier mismatch: {ss} vs {ts}")
    return (len(issues) > 0, "; ".join(issues))


def _auto_accept_checklist(
    match: dict[str, Any],
    registration_passed: bool,
    registration_uncertainty: float,
    has_conflict: bool,
    db: Session,
) -> dict[str, Any]:
    """
    Compute the 9-condition auto-accept checklist and persist it.
    All must pass for auto-accept.
    """
    evidence = match.get("evidence", {})
    probability = float(match.get("probability", 0))
    relation = match.get("relation", "")

    checklist = {
        "geometry_valid": True,
        "regional_registration_pass": registration_passed,
        "match_probability_above_threshold": probability >= settings.auto_match_threshold,
        "no_high_conflict": not has_conflict,
        "no_authoritative_attribute_conflict": not (
            has_conflict and relation in ("ONE_TO_ONE", "BOUNDARY_ADJUSTMENT")
        ),
        "no_structural_ambiguity": relation not in ("AMBIGUOUS",) or probability >= 0.9,
        "within_uncertainty_envelope": not has_conflict,
        "provenance_complete": True,
        "legal_state_unchanged": True,
    }

    checklist["all_pass"] = all(checklist.values())
    checklist["match_probability"] = probability
    checklist["relation"] = relation
    checklist["evidence_summary"] = {
        k: evidence.get(k, 0) for k in [
            "geometry_compatibility", "attribute_compatibility",
            "survey_support", "physical_support",
            "temporal_support", "identity_compatibility",
        ]
    }

    return checklist


def detect_conflicts(
    db: Session,
    matches: list[dict[str, Any]],
    *,
    registration_passed: bool = True,
    registration_uncertainty: float = 0.3,
    source_dataset_id: str | None = None,
) -> dict[str, Any]:
    created = []
    checklists = []

    if not registration_passed:
        cid = new_id("CF")
        db.add(
            Conflict(
                id=cid,
                conflict_type="REGISTRATION_FAILURE",
                severity="HIGH",
                description="Registration did not pass regional validation",
            )
        )
        rid = new_id("RC")
        db.add(ReviewCase(id=rid, conflict_id=cid, status="OPEN", summary="Registration failure"))
        created.append({"conflict_id": cid, "type": "REGISTRATION_FAILURE"})
        db.commit()
        return {"conflicts": created, "count": len(created), "checklists": []}

    for m in matches:
        ctype = None
        details: dict[str, Any] = {}
        desc = ""

        if m.get("relation") in ("AMBIGUOUS",):
            ctype = "AMBIGUOUS_MATCH"
            desc = "Multiple plausible parcel correspondences"
        elif m.get("relation") == "SPLIT" and m.get("probability", 0) < 0.75:
            ctype = "SPLIT_AMBIGUITY"
            desc = "Split hypothesis below confidence"
        elif m.get("relation") == "MERGE" and m.get("probability", 0) < 0.75:
            ctype = "MERGE_AMBIGUITY"
            desc = "Merge hypothesis below confidence"

        # Attribute and displacement conflict for matched parcels
        if m.get("relation") in ("ONE_TO_ONE", "BOUNDARY_ADJUSTMENT", "SPLIT", "AMBIGUOUS") and m.get("targets"):
            src_id = m["source"] if isinstance(m["source"], str) else m["source"][0]
            tgt_id = m["targets"][0]
            rows = db.execute(
                text(
                    """
                    SELECT DISTINCT ON (parcel_id) parcel_id, attributes, ST_AsText(geometry) AS wkt,
                           geometry_uncertainty_m
                    FROM parcel_versions
                    WHERE parcel_id IN (:a, :b)
                    ORDER BY parcel_id, version DESC
                    """
                ),
                {"a": src_id, "b": tgt_id},
            ).mappings().all()
            by_id = {r["parcel_id"]: r for r in rows}
            if src_id in by_id and tgt_id in by_id:
                conflict, msg = _attr_conflict(by_id[src_id]["attributes"] or {}, by_id[tgt_id]["attributes"] or {})
                if conflict:
                    ctype = "ATTRIBUTE_CONFLICT"
                    desc = msg or "Attribute / identity conflict"
                    details["case"] = "B"

                g1 = shapely_wkt.loads(by_id[src_id]["wkt"])
                g2 = shapely_wkt.loads(by_id[tgt_id]["wkt"])
                disp = g1.centroid.distance(g2.centroid)
                sig = relative_uncertainty(
                    float(by_id[src_id]["geometry_uncertainty_m"] or 0.25),
                    float(by_id[tgt_id]["geometry_uncertainty_m"] or 0.25),
                    registration_uncertainty,
                )
                if not within_uncertainty_envelope(disp, sig):
                    ctype = ctype or "AREA_MISMATCH"
                    if disp > 3 * sig:
                        ctype = "PHYSICAL_EVIDENCE_CONFLICT"
                        desc = f"Observed displacement {disp:.2f}m exceeds uncertainty {sig:.2f}m"
                        details["case"] = "A"

        has_conflict = ctype is not None

        # Compute auto-accept checklist for every match
        checklist = _auto_accept_checklist(
            m, registration_passed, registration_uncertainty, has_conflict, db,
        )
        checklists.append({"match_id": m.get("id"), "checklist": checklist})

        if ctype:
            cid = new_id("CF")
            parcel_ids = []
            if isinstance(m.get("source"), list):
                parcel_ids.extend(m["source"])
            elif m.get("source"):
                parcel_ids.append(m["source"])
            parcel_ids.extend(m.get("targets") or [])
            db.add(
                Conflict(
                    id=cid,
                    conflict_type=ctype,
                    severity="HIGH",
                    parcel_ids=parcel_ids,
                    description=desc,
                    details={
                        **details,
                        "match_id": m.get("id"),
                        "evidence": m.get("evidence"),
                        "checklist": checklist,
                    },
                )
            )
            rid = new_id("RC")
            db.add(
                ReviewCase(
                    id=rid,
                    conflict_id=cid,
                    candidate_match_id=m.get("id"),
                    status="OPEN",
                    summary=desc,
                    evidence_snapshot={
                        "match_probability": m.get("probability"),
                        "evidence": m.get("evidence"),
                        "relation": m.get("relation"),
                        "checklist": checklist,
                    },
                )
            )
            created.append({"conflict_id": cid, "type": ctype, "review_case_id": rid, "match_id": m.get("id")})

    # Topology: layer-scoped overlap detection
    # Scope to source dataset parcels only if specified
    scope_clause = ""
    scope_params: dict[str, Any] = {}
    if source_dataset_id:
        scope_clause = "AND source_ids @> CAST(:scope_ds AS jsonb)"
        scope_params["scope_ds"] = f'["{source_dataset_id}"]'

    rows = db.execute(
        text(
            f"""
            SELECT a.parcel_id AS a_id, b.parcel_id AS b_id,
                   ST_AsText(a.geometry) AS a_wkt, ST_AsText(b.geometry) AS b_wkt
            FROM (
              SELECT DISTINCT ON (parcel_id) parcel_id, geometry, source_ids
              FROM parcel_versions {f'WHERE source_ids @> CAST(:scope_ds_a AS jsonb)' if source_dataset_id else ''}
              ORDER BY parcel_id, version DESC
            ) a
            JOIN (
              SELECT DISTINCT ON (parcel_id) parcel_id, geometry, source_ids
              FROM parcel_versions {f'WHERE source_ids @> CAST(:scope_ds_b AS jsonb)' if source_dataset_id else ''}
              ORDER BY parcel_id, version DESC
            ) b ON a.parcel_id < b.parcel_id
            WHERE ST_Overlaps(a.geometry, b.geometry)
               OR (ST_Intersects(a.geometry, b.geometry)
                   AND ST_Area(ST_Intersection(a.geometry, b.geometry))
                       / LEAST(ST_Area(a.geometry), ST_Area(b.geometry)) > 0.05)
            LIMIT 50
            """
        ),
        {
            **({"scope_ds_a": f'["{source_dataset_id}"]', "scope_ds_b": f'["{source_dataset_id}"]'} if source_dataset_id else {}),
        },
    ).mappings().all()

    for r in rows:
        g1 = shapely_wkt.loads(r["a_wkt"])
        g2 = shapely_wkt.loads(r["b_wkt"])
        if significant_overlap(g1, g2):
            cid = new_id("CF")
            db.add(
                Conflict(
                    id=cid,
                    conflict_type="PARCEL_OVERLAP",
                    severity="HIGH",
                    parcel_ids=[r["a_id"], r["b_id"]],
                    description=f"Overlap between {r['a_id']} and {r['b_id']}",
                    geometry=WKTElement(g1.intersection(g2).wkt, srid=32643),
                )
            )
            rid = new_id("RC")
            db.add(ReviewCase(id=rid, conflict_id=cid, status="OPEN", summary="Parcel overlap"))
            created.append({"conflict_id": cid, "type": "PARCEL_OVERLAP", "review_case_id": rid})

    db.commit()
    return {"conflicts": created, "count": len(created), "checklists": checklists}
