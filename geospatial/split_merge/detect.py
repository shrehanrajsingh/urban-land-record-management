"""Split / merge / boundary-adjustment detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geospatial.geometry.features import max_pairwise_overlap, union_coverage

Relation = Literal["ONE_TO_ONE", "SPLIT", "MERGE", "BOUNDARY_ADJUSTMENT", "NO_MATCH", "AMBIGUOUS"]


@dataclass
class StructureResult:
    relation: Relation
    coverage: float
    mutual_overlap: float
    area_ratio: float
    accepted: bool
    details: dict


def area_ratio_within_tolerance(ratio: float, tolerance: float = 0.15) -> bool:
    return abs(1.0 - ratio) <= tolerance


def detect_split(
    parent: BaseGeometry,
    candidates: list[BaseGeometry],
    coverage_threshold: float = 0.85,
    overlap_threshold: float = 0.10,
    area_tolerance: float = 0.15,
) -> StructureResult:
    if len(candidates) < 2:
        return StructureResult("NO_MATCH", 0.0, 0.0, 0.0, False, {"reason": "need_ge_2_children"})

    coverage = union_coverage(parent, candidates)
    mutual = max_pairwise_overlap(candidates)
    area_ratio = sum(c.area for c in candidates) / parent.area if parent.area else 0.0

    child_ok = all(
        (parent.intersection(c).area / c.area) >= 0.5 if c.area else False for c in candidates
    )
    accepted = (
        child_ok
        and coverage >= coverage_threshold
        and mutual <= overlap_threshold
        and area_ratio_within_tolerance(area_ratio, area_tolerance)
    )
    return StructureResult(
        relation="SPLIT" if accepted else "AMBIGUOUS",
        coverage=coverage,
        mutual_overlap=mutual,
        area_ratio=area_ratio,
        accepted=accepted,
        details={"child_ok": child_ok},
    )


def detect_merge(
    children: list[BaseGeometry],
    parent: BaseGeometry,
    coverage_threshold: float = 0.85,
    overlap_threshold: float = 0.10,
    area_tolerance: float = 0.15,
) -> StructureResult:
    """Inverse of split: multiple old parcels vs one new."""
    if len(children) < 2:
        return StructureResult("NO_MATCH", 0.0, 0.0, 0.0, False, {"reason": "need_ge_2_sources"})

    u = unary_union(children)
    coverage = float(u.intersection(parent).area / parent.area) if parent.area else 0.0
    mutual = max_pairwise_overlap(children)
    area_ratio = u.area / parent.area if parent.area else 0.0
    accepted = (
        coverage >= coverage_threshold
        and mutual <= overlap_threshold
        and area_ratio_within_tolerance(area_ratio, area_tolerance)
    )
    return StructureResult(
        relation="MERGE" if accepted else "AMBIGUOUS",
        coverage=coverage,
        mutual_overlap=mutual,
        area_ratio=area_ratio,
        accepted=accepted,
        details={},
    )


def detect_boundary_adjustment(
    a: BaseGeometry,
    b: BaseGeometry,
    iou_threshold: float = 0.7,
    centroid_threshold: float = 5.0,
) -> StructureResult:
    inter = a.intersection(b).area
    union = a.union(b).area
    iou = inter / union if union else 0.0
    cd = a.centroid.distance(b.centroid)
    accepted = iou >= iou_threshold and cd <= centroid_threshold and abs(a.area - b.area) / max(a.area, 1e-9) < 0.25
    return StructureResult(
        relation="BOUNDARY_ADJUSTMENT" if accepted else "ONE_TO_ONE",
        coverage=iou,
        mutual_overlap=0.0,
        area_ratio=a.area / b.area if b.area else 0.0,
        accepted=accepted,
        details={"iou": iou, "centroid_distance": cd},
    )
