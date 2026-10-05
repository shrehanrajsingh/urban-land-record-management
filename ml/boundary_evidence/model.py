"""Boundary-evidence model interface (YOLO-seg initially)."""

from __future__ import annotations

from typing import Any


CLASSES = [
    "BUILDING_EDGE",
    "WALL",
    "FENCE",
    "ROAD_EDGE",
    "DRAIN",
    "SURVEY_MARK",
    "VISIBLE_PLOT_DIVISION",
    "VEGETATION_BOUNDARY",
    "OTHER",
]


def predict_patches(image_patches: list[Any] | None = None) -> list[dict[str, Any]]:
    """
    MVP: training/inference against a few hundred labelled patches is Phase-competition optional.
    Pipeline uses extract_physical_evidence_stub for demo observations.
    """
    return []
