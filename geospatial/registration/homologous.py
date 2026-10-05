"""Extract matching corner points from two parcel layers."""

from __future__ import annotations

import numpy as np
import geopandas as gpd
from shapely.geometry import MultiPoint


def _extract_corners(gdf: gpd.GeoDataFrame) -> np.ndarray:
    """Return unique corner vertices from all parcel geometries."""
    pts: list[tuple[float, float]] = []
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        coords = list(geom.exterior.coords) if hasattr(geom, "exterior") else []
        # Drop the closing duplicate vertex
        if len(coords) > 1 and coords[0] == coords[-1]:
            coords = coords[:-1]
        pts.extend(coords)
    if not pts:
        return np.empty((0, 2), dtype=float)
    arr = np.array(pts, dtype=float)
    # De-duplicate using rounded coordinates (sub-mm precision)
    _, idx = np.unique(np.round(arr, decimals=4), axis=0, return_index=True)
    return arr[np.sort(idx)]


def _edge_angle_at_vertex(geom, vertex_idx: int) -> float:
    """Compute the interior angle (degrees) at a given vertex of a polygon ring."""
    coords = list(geom.exterior.coords)
    if coords[0] == coords[-1]:
        coords = coords[:-1]
    n = len(coords)
    if n < 3:
        return 180.0
    idx = vertex_idx % n
    p_prev = np.array(coords[(idx - 1) % n])
    p_curr = np.array(coords[idx])
    p_next = np.array(coords[(idx + 1) % n])
    v1 = p_prev - p_curr
    v2 = p_next - p_curr
    cos_a = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-12)
    return float(np.degrees(np.arccos(np.clip(cos_a, -1.0, 1.0))))


def _vertex_angle_map(gdf: gpd.GeoDataFrame) -> dict[tuple[float, float], float]:
    """Build a mapping from (rounded) vertex coordinate to its interior angle."""
    angle_map: dict[tuple[float, float], float] = {}
    for geom in gdf.geometry:
        if geom is None or geom.is_empty or not hasattr(geom, "exterior"):
            continue
        coords = list(geom.exterior.coords)
        if coords[0] == coords[-1]:
            coords = coords[:-1]
        for i in range(len(coords)):
            key = (round(coords[i][0], 4), round(coords[i][1], 4))
            if key not in angle_map:
                angle_map[key] = _edge_angle_at_vertex(geom, i)
    return angle_map


def _assign_region(
    xy: tuple[float, float], med_x: float, med_y: float
) -> str:
    """Assign NE/NW/SE/SW based on median coordinates."""
    ew = "E" if xy[0] >= med_x else "W"
    ns = "N" if xy[1] >= med_y else "S"
    return ns + ew


def extract_homologous_points(
    source_gdf: gpd.GeoDataFrame,
    target_gdf: gpd.GeoDataFrame,
    max_points: int = 100,
    coarse_search_radius: float = 50.0,
) -> list[tuple[tuple[float, float], tuple[float, float], str]]:
    """Extract matching corner points from two parcel layers.

    Steps:
    1. Extract all parcel corner vertices from both layers.
    2. Compute a coarse centroid offset between the two layers' bounding
       box centres.
    3. After applying the coarse offset, for each source corner find the
       nearest target corner within *coarse_search_radius*.
    4. Filter by local geometry agreement: keep pairs whose interior
       angles at the matched corners agree within 30 degrees.
    5. Assign a region label (NE/NW/SE/SW based on median coordinates).
    6. Return list of (source_xy, target_xy, region).
    """
    src_corners = _extract_corners(source_gdf)
    tgt_corners = _extract_corners(target_gdf)

    if len(src_corners) == 0 or len(tgt_corners) == 0:
        return []

    # Coarse centroid offset from bounding-box centres
    src_bbox = source_gdf.total_bounds  # [minx, miny, maxx, maxy]
    tgt_bbox = target_gdf.total_bounds
    src_centre = np.array([(src_bbox[0] + src_bbox[2]) / 2, (src_bbox[1] + src_bbox[3]) / 2])
    tgt_centre = np.array([(tgt_bbox[0] + tgt_bbox[2]) / 2, (tgt_bbox[1] + tgt_bbox[3]) / 2])
    offset = tgt_centre - src_centre

    # Shift source corners by coarse offset for matching
    shifted_src = src_corners + offset

    # Build angle maps for filtering
    src_angles = _vertex_angle_map(source_gdf)
    tgt_angles = _vertex_angle_map(target_gdf)

    # For each shifted source corner, find the nearest target corner
    from scipy.spatial import cKDTree

    tree = cKDTree(tgt_corners)
    dists, indices = tree.query(shifted_src, k=1)

    pairs: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    for i in range(len(src_corners)):
        if dists[i] > coarse_search_radius:
            continue
        j = indices[i]
        src_xy = (float(src_corners[i, 0]), float(src_corners[i, 1]))
        tgt_xy = (float(tgt_corners[j, 0]), float(tgt_corners[j, 1]))

        # Angle agreement filter
        src_key = (round(src_xy[0], 4), round(src_xy[1], 4))
        tgt_key = (round(tgt_xy[0], 4), round(tgt_xy[1], 4))
        src_angle = src_angles.get(src_key, 90.0)
        tgt_angle = tgt_angles.get(tgt_key, 90.0)
        if abs(src_angle - tgt_angle) > 30.0:
            continue

        pairs.append((src_xy, tgt_xy, dists[i]))

    # Sort by distance (best matches first) and cap at max_points
    pairs.sort(key=lambda t: t[2])
    pairs = pairs[:max_points]

    # Region labelling based on median source coordinates
    if not pairs:
        return []
    src_pts = np.array([p[0] for p in pairs])
    med_x = float(np.median(src_pts[:, 0]))
    med_y = float(np.median(src_pts[:, 1]))

    result: list[tuple[tuple[float, float], tuple[float, float], str]] = []
    for src_xy, tgt_xy, _ in pairs:
        region = _assign_region(src_xy, med_x, med_y)
        result.append((src_xy, tgt_xy, region))

    return result
