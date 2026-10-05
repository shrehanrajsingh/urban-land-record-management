"""Pairwise geometric feature computation for conflation."""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def _safe_ratio(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def centroid_distance(a: BaseGeometry, b: BaseGeometry) -> float:
    return float(a.centroid.distance(b.centroid))


def boundary_hausdorff(a: BaseGeometry, b: BaseGeometry) -> float:
    try:
        return float(a.boundary.hausdorff_distance(b.boundary))
    except Exception:
        return float(a.hausdorff_distance(b))


def iou(a: BaseGeometry, b: BaseGeometry) -> float:
    inter = a.intersection(b).area
    union = a.union(b).area
    return float(_safe_ratio(inter, union))


def area_ratio(a: BaseGeometry, b: BaseGeometry) -> float:
    return float(_safe_ratio(min(a.area, b.area), max(a.area, b.area)))


def perimeter_ratio(a: BaseGeometry, b: BaseGeometry) -> float:
    return float(_safe_ratio(min(a.length, b.length), max(a.length, b.length)))


def orientation_similarity(a: BaseGeometry, b: BaseGeometry) -> float:
    """Approximate orientation via minimum rotated rectangle major-axis angle."""

    def angle(g: BaseGeometry) -> float:
        try:
            mrr = g.minimum_rotated_rectangle
            if mrr.geom_type != "Polygon":
                return 0.0
            coords = list(mrr.exterior.coords)
            if len(coords) < 2:
                return 0.0
            dx = coords[1][0] - coords[0][0]
            dy = coords[1][1] - coords[0][1]
            return math.atan2(dy, dx)
        except Exception:
            return 0.0

    da = abs(angle(a) - angle(b))
    da = min(da, math.pi - da)
    return float(1.0 - da / (math.pi / 2))


def shape_similarity(a: BaseGeometry, b: BaseGeometry) -> float:
    """Compactness-based shape similarity."""

    def compactness(g: BaseGeometry) -> float:
        if g.length == 0:
            return 0.0
        return 4 * math.pi * g.area / (g.length**2)

    ca, cb = compactness(a), compactness(b)
    return float(_safe_ratio(min(ca, cb), max(ca, cb) if max(ca, cb) else 1.0))


def attribute_similarity(attrs_a: dict[str, Any], attrs_b: dict[str, Any]) -> float:
    keys = {"parcel_id", "survey_no", "owner_name", "land_use", "revenue_area"}
    scores = []
    for k in keys:
        if k not in attrs_a and k not in attrs_b:
            continue
        va, vb = attrs_a.get(k), attrs_b.get(k)
        if va is None or vb is None:
            scores.append(0.0)
        elif isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            denom = max(abs(va), abs(vb), 1e-9)
            scores.append(max(0.0, 1.0 - abs(va - vb) / denom))
        else:
            scores.append(1.0 if str(va).strip().lower() == str(vb).strip().lower() else 0.0)
    return float(sum(scores) / len(scores)) if scores else 0.5


def build_pair_features(
    source_geom: BaseGeometry,
    target_geom: BaseGeometry,
    source_attrs: dict[str, Any] | None = None,
    target_attrs: dict[str, Any] | None = None,
    gnss_support: float = 0.0,
    building_support: float = 0.0,
) -> dict[str, float]:
    return {
        "centroid_distance": centroid_distance(source_geom, target_geom),
        "boundary_distance": boundary_hausdorff(source_geom, target_geom),
        "iou": iou(source_geom, target_geom),
        "intersection_area": float(source_geom.intersection(target_geom).area),
        "area_ratio": area_ratio(source_geom, target_geom),
        "perimeter_ratio": perimeter_ratio(source_geom, target_geom),
        "shape_similarity": shape_similarity(source_geom, target_geom),
        "orientation_similarity": orientation_similarity(source_geom, target_geom),
        "attribute_similarity": attribute_similarity(source_attrs or {}, target_attrs or {}),
        "gnss_support": float(gnss_support),
        "building_support": float(building_support),
    }


def union_coverage(parent: BaseGeometry, children: list[BaseGeometry]) -> float:
    intersections = [parent.intersection(c) for c in children if not parent.intersection(c).is_empty]
    if not intersections:
        return 0.0
    u = unary_union(intersections)
    return float(_safe_ratio(u.area, parent.area))


def max_pairwise_overlap(geoms: list[BaseGeometry]) -> float:
    max_ov = 0.0
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            inter = geoms[i].intersection(geoms[j]).area
            denom = min(geoms[i].area, geoms[j].area)
            if denom > 0:
                max_ov = max(max_ov, inter / denom)
    return float(max_ov)
