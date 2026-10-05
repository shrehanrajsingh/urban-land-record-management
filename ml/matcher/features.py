"""Feature vector helpers for the match probability model."""

from __future__ import annotations

FEATURE_COLUMNS = [
    "centroid_distance",
    "boundary_distance",
    "iou",
    "intersection_area",
    "area_ratio",
    "perimeter_ratio",
    "shape_similarity",
    "orientation_similarity",
    "attribute_similarity",
    "gnss_support",
    "building_support",
]


def features_to_vector(features: dict[str, float]) -> list[float]:
    return [float(features.get(c, 0.0)) for c in FEATURE_COLUMNS]
