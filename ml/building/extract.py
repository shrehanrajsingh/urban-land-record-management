"""Building & wall extraction from synthetic ORI imagery using image processing."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union


def _load_image(path: str) -> tuple[np.ndarray, dict]:
    """Load a GeoTIFF or plain image with its geotransform."""
    p = Path(path)
    meta = {"gsd": 0.25, "origin_x": 0.0, "origin_y": 0.0, "width": 0, "height": 0}

    try:
        import rasterio
        with rasterio.open(path) as ds:
            img = ds.read()  # (bands, H, W)
            if img.shape[0] >= 3:
                img = np.moveaxis(img[:3], 0, -1)  # (H, W, 3)
            else:
                img = np.moveaxis(img, 0, -1)
            t = ds.transform
            meta["gsd"] = t.a
            meta["origin_x"] = t.c
            meta["origin_y"] = t.f
            meta["width"] = ds.width
            meta["height"] = ds.height
        return img, meta
    except ImportError:
        pass

    try:
        from PIL import Image
        pil = Image.open(path).convert("RGB")
        img = np.array(pil)
        meta["width"] = img.shape[1]
        meta["height"] = img.shape[0]
        # Check for world file
        tfw = p.with_suffix(".tfw")
        if tfw.exists():
            lines = tfw.read_text().strip().split("\n")
            if len(lines) >= 6:
                meta["gsd"] = abs(float(lines[0]))
                meta["origin_x"] = float(lines[4])
                meta["origin_y"] = float(lines[5])
        return img, meta
    except ImportError:
        raise ImportError("Neither rasterio nor PIL available for image loading")


def _pixel_to_world(row: int, col: int, meta: dict) -> tuple[float, float]:
    """Convert pixel coordinates to world coordinates."""
    x = meta["origin_x"] + col * meta["gsd"]
    y = meta["origin_y"] - row * meta["gsd"]
    return x, y


def _world_to_pixel(x: float, y: float, meta: dict) -> tuple[int, int]:
    """Convert world coordinates to pixel coordinates."""
    col = int((x - meta["origin_x"]) / meta["gsd"])
    row = int((meta["origin_y"] - y) / meta["gsd"])
    return row, col


def _extract_building_mask(img: np.ndarray) -> np.ndarray:
    """
    Extract building footprints using color segmentation.
    Buildings in synthetic ORI have distinct roof colors (red-brown, grey, blue-grey)
    against green vegetation background.
    """
    if img.ndim != 3 or img.shape[2] < 3:
        return np.zeros(img.shape[:2], dtype=np.uint8)

    r, g, b = img[:, :, 0].astype(float), img[:, :, 1].astype(float), img[:, :, 2].astype(float)

    # Detect non-vegetation: areas where green doesn't dominate
    greenness = g - (r + b) / 2
    not_green = greenness < 15

    # Detect non-road: roads are grey (R≈G≈B) with moderate brightness
    brightness = (r + g + b) / 3
    saturation = np.max(img, axis=2).astype(float) - np.min(img, axis=2).astype(float)
    is_grey_road = (saturation < 30) & (brightness > 80) & (brightness < 180)

    # Buildings: not green, not road-grey, with some structure
    building_mask = not_green & ~is_grey_road & (brightness > 40)

    # Morphological cleanup
    from scipy.ndimage import binary_closing, binary_opening, label

    kernel_size = max(3, int(1.0 / 0.25))  # ~1m kernel
    building_mask = binary_closing(building_mask, iterations=2)
    building_mask = binary_opening(building_mask, iterations=2)

    # Remove small regions (< 4 sq meters at 0.25m GSD = 64 pixels)
    labeled, n_features = label(building_mask)
    min_area_pixels = 64
    clean_mask = np.zeros_like(building_mask)
    for i in range(1, n_features + 1):
        region = labeled == i
        if region.sum() >= min_area_pixels:
            clean_mask |= region

    return clean_mask.astype(np.uint8)


def _extract_wall_edges(img: np.ndarray) -> np.ndarray:
    """
    Extract linear boundary features (walls, fences) using edge detection.
    Walls appear as thin dark lines in the synthetic ORI.
    """
    if img.ndim != 3:
        grey = img
    else:
        grey = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]).astype(np.uint8)

    # Sobel-like edge detection
    from scipy.ndimage import sobel, binary_dilation

    sx = sobel(grey.astype(float), axis=1)
    sy = sobel(grey.astype(float), axis=0)
    edge_magnitude = np.sqrt(sx ** 2 + sy ** 2)

    # Dark lines: low brightness AND high edge magnitude nearby
    dark = grey < 80
    edges = edge_magnitude > np.percentile(edge_magnitude[edge_magnitude > 0], 85) if edge_magnitude.max() > 0 else np.zeros_like(dark)
    wall_mask = dark & binary_dilation(edges, iterations=1)

    return wall_mask.astype(np.uint8)


def _mask_to_polygons(
    mask: np.ndarray,
    meta: dict,
    simplify_tolerance: float = 0.5,
) -> list[Polygon]:
    """Convert a binary mask to world-coordinate polygons."""
    from scipy.ndimage import label

    labeled, n_features = label(mask)
    polygons = []

    for i in range(1, n_features + 1):
        region = labeled == i
        rows, cols = np.where(region)
        if len(rows) < 4:
            continue

        # Compute bounding box in pixel space
        r_min, r_max = rows.min(), rows.max()
        c_min, c_max = cols.min(), cols.max()

        # Convert to world coordinates
        x_min, y_max = _pixel_to_world(r_min, c_min, meta)
        x_max, y_min = _pixel_to_world(r_max, c_max, meta)

        # Create polygon from convex hull of region pixels
        # Sample boundary pixels for a better shape
        boundary_pixels = []
        for r in range(r_min, r_max + 1):
            row_cols = cols[rows == r]
            if len(row_cols) > 0:
                boundary_pixels.append((r, row_cols.min()))
                boundary_pixels.append((r, row_cols.max()))

        if len(boundary_pixels) < 3:
            polygons.append(box(x_min, y_min, x_max, y_max))
            continue

        # Convert boundary pixels to world coords
        world_pts = [_pixel_to_world(r, c, meta) for r, c in boundary_pixels]

        try:
            from shapely.geometry import MultiPoint
            hull = MultiPoint(world_pts).convex_hull
            if hull.geom_type == "Polygon" and hull.area > 0:
                simplified = hull.simplify(simplify_tolerance)
                if simplified.geom_type == "Polygon" and simplified.area > 0:
                    polygons.append(simplified)
                else:
                    polygons.append(hull)
            else:
                polygons.append(box(x_min, y_min, x_max, y_max))
        except Exception:
            polygons.append(box(x_min, y_min, x_max, y_max))

    return polygons


def _mask_to_lines(
    mask: np.ndarray,
    meta: dict,
    min_length_m: float = 3.0,
) -> list[LineString]:
    """Convert a wall/edge mask to world-coordinate lines using skeletonization."""
    from scipy.ndimage import label

    labeled, n_features = label(mask)
    lines = []

    for i in range(1, n_features + 1):
        region = labeled == i
        rows, cols = np.where(region)
        if len(rows) < 4:
            continue

        # Fit a principal axis line through the region pixels
        pts = np.column_stack([cols, rows]).astype(float)
        mean = pts.mean(axis=0)
        centered = pts - mean

        # SVD for principal direction
        try:
            _, s, vt = np.linalg.svd(centered, full_matrices=False)
        except np.linalg.LinAlgError:
            continue

        direction = vt[0]
        projections = centered @ direction
        p_min, p_max = projections.min(), projections.max()

        start_px = mean + direction * p_min
        end_px = mean + direction * p_max

        start_world = _pixel_to_world(int(start_px[1]), int(start_px[0]), meta)
        end_world = _pixel_to_world(int(end_px[1]), int(end_px[0]), meta)

        line = LineString([start_world, end_world])
        if line.length >= min_length_m:
            lines.append(line)

    return lines


def extract_buildings(
    ori_path: str | None = None,
    dsm_path: str | None = None,
    dtm_path: str | None = None,
    parcels_bounds: tuple[float, float, float, float] | None = None,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """
    Extract building footprints from ORI imagery.
    When DSM and DTM are both available, compute building heights.
    Returns list of {type, geometry_wkt, confidence, uncertainty_m, height_m, area_m2}.
    """
    if ori_path is None:
        return []

    try:
        img, meta = _load_image(ori_path)
    except Exception:
        return []

    building_mask = _extract_building_mask(img)
    polygons = _mask_to_polygons(building_mask, meta)

    # Compute heights from DSM - DTM if both available
    dsm_data = None
    dtm_data = None
    if dsm_path and dtm_path:
        try:
            dsm_data, dsm_meta = _load_image(dsm_path)
            dtm_data, dtm_meta = _load_image(dtm_path)
            if dsm_data.ndim == 3:
                dsm_data = dsm_data[:, :, 0]
            if dtm_data.ndim == 3:
                dtm_data = dtm_data[:, :, 0]
        except Exception:
            dsm_data = None
            dtm_data = None

    results = []
    rng = np.random.default_rng(seed)

    for poly in polygons:
        area = poly.area
        if area < 4.0:  # skip tiny artifacts
            continue

        # Confidence based on area and shape regularity
        compactness = 4 * math.pi * area / (poly.length ** 2) if poly.length > 0 else 0
        confidence = min(0.98, 0.75 + 0.2 * compactness + rng.uniform(-0.02, 0.02))

        # Positional uncertainty: larger buildings have lower relative uncertainty
        uncertainty = max(0.1, 0.3 - 0.01 * math.sqrt(area))

        height = None
        if dsm_data is not None and dtm_data is not None:
            # Sample height at building centroid
            cx, cy = poly.centroid.x, poly.centroid.y
            try:
                dr, dc = _world_to_pixel(cx, cy, dsm_meta)
                if 0 <= dr < dsm_data.shape[0] and 0 <= dc < dsm_data.shape[1]:
                    dsm_val = float(dsm_data[dr, dc])
                    dtm_val = float(dtm_data[min(dr, dtm_data.shape[0]-1), min(dc, dtm_data.shape[1]-1)])
                    height = max(0.0, dsm_val - dtm_val)
            except (IndexError, ValueError):
                pass

        results.append({
            "type": "BUILDING",
            "geometry_wkt": poly.wkt,
            "confidence": round(float(confidence), 3),
            "uncertainty_m": round(float(uncertainty), 3),
            "height_m": round(float(height), 2) if height is not None else None,
            "area_m2": round(float(area), 2),
        })

    return results


def extract_walls(
    ori_path: str | None = None,
    parcels_bounds: tuple[float, float, float, float] | None = None,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """
    Extract linear boundary features (walls, fences, edges) from ORI imagery.
    Returns list of {type, geometry_wkt, confidence, uncertainty_m, length_m}.
    """
    if ori_path is None:
        return []

    try:
        img, meta = _load_image(ori_path)
    except Exception:
        return []

    wall_mask = _extract_wall_edges(img)
    lines = _mask_to_lines(wall_mask, meta)

    rng = np.random.default_rng(seed)
    results = []

    for line in lines:
        confidence = min(0.95, 0.70 + 0.05 * math.log1p(line.length) + rng.uniform(-0.03, 0.03))
        uncertainty = max(0.1, 0.25 - 0.005 * line.length)

        results.append({
            "type": "WALL",
            "geometry_wkt": line.wkt,
            "confidence": round(float(confidence), 3),
            "uncertainty_m": round(float(uncertainty), 3),
            "length_m": round(float(line.length), 2),
        })

    return results


def extract_all(
    ori_path: str | None = None,
    dsm_path: str | None = None,
    dtm_path: str | None = None,
    parcels_bounds: tuple[float, float, float, float] | None = None,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Extract both buildings and walls, returning combined observations."""
    buildings = extract_buildings(ori_path, dsm_path, dtm_path, parcels_bounds, seed)
    walls = extract_walls(ori_path, parcels_bounds, seed)
    return buildings + walls


def polygonize_mask(mask_polygon: Polygon, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "type": "BUILDING",
        "geometry": mask_polygon.wkt,
        "confidence": confidence,
        "uncertainty_m": 0.18,
    }
