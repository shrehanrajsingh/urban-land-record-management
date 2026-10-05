"""Safe geometric auto-corrections only (deterministic computational errors)."""

from __future__ import annotations

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid


def is_tiny_sliver(geom: BaseGeometry, min_area: float = 0.01) -> bool:
    return geom.area < min_area


def auto_fix_geometry(geom: BaseGeometry) -> tuple[BaseGeometry, list[str]]:
    """Only fix ring-not-closed, duplicate vertices, tiny slivers, invalid encoding."""
    fixes: list[str] = []
    g = geom

    if not g.is_valid:
        g = make_valid(g)
        fixes.append("make_valid")

    if g.geom_type == "MultiPolygon":
        # pick largest polygon for cadastral MVP simplicity
        g = max(g.geoms, key=lambda x: x.area)
        fixes.append("multipart_to_largest")

    if g.geom_type == "Polygon":
        coords = list(g.exterior.coords)
        # remove consecutive duplicates
        cleaned = [coords[0]]
        for c in coords[1:]:
            if c != cleaned[-1]:
                cleaned.append(c)
        if cleaned[0] != cleaned[-1]:
            cleaned.append(cleaned[0])
            fixes.append("close_ring")
        if len(cleaned) != len(coords):
            fixes.append("dedupe_vertices")
        g = Polygon(cleaned, [list(r.coords) for r in g.interiors])

    return g, fixes


def significant_overlap(a: BaseGeometry, b: BaseGeometry, threshold: float = 0.05) -> bool:
    inter = a.intersection(b).area
    denom = min(a.area, b.area)
    return denom > 0 and (inter / denom) >= threshold
