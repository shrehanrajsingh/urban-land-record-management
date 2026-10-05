"""Synthetic ORI / DSM / DTM renderer for benchmark parcels.

Produces GeoTIFF imagery (or plain images with world files when rasterio is
unavailable) suitable for visual inspection and ML boundary-evidence pipelines.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

try:
    import rasterio
    from rasterio.transform import from_bounds

    _HAS_RASTERIO = True
except ImportError:
    _HAS_RASTERIO = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bounds_with_buffer(gdf: gpd.GeoDataFrame, buf: float = 10.0) -> tuple[float, float, float, float]:
    b = gdf.total_bounds  # minx, miny, maxx, maxy
    return (b[0] - buf, b[1] - buf, b[2] + buf, b[3] + buf)


def _world_to_pixel(x: float, y: float, minx: float, maxy: float, gsd: float) -> tuple[int, int]:
    """Convert world coordinate to pixel (col, row)."""
    col = int(round((x - minx) / gsd))
    row = int(round((maxy - y) / gsd))
    return col, row


def _geom_to_pixel_coords(
    geom: Polygon, minx: float, maxy: float, gsd: float,
) -> list[tuple[int, int]]:
    """Convert polygon exterior to pixel coordinate list for PIL."""
    coords = list(geom.exterior.coords)
    return [_world_to_pixel(x, y, minx, maxy, gsd) for x, y in coords]


def _write_world_file(path: str, minx: float, maxy: float, gsd: float) -> str:
    """Write a .tfw / .pgw world file alongside the image."""
    p = Path(path)
    ext = p.suffix.lower()
    wf_ext = {".tif": ".tfw", ".tiff": ".tfw", ".png": ".pgw", ".jpg": ".jgw"}.get(ext, ".tfw")
    wf_path = p.with_suffix(wf_ext)
    with open(wf_path, "w") as f:
        f.write(f"{gsd}\n")
        f.write("0.0\n")
        f.write("0.0\n")
        f.write(f"{-gsd}\n")
        f.write(f"{minx + gsd / 2}\n")
        f.write(f"{maxy - gsd / 2}\n")
    return str(wf_path)


def _write_geotiff_rgb(
    arr: np.ndarray, path: str, bounds: tuple, crs: str,
) -> None:
    """Write RGB array as GeoTIFF using rasterio."""
    minx, miny, maxx, maxy = bounds
    height, width = arr.shape[:2]
    transform = from_bounds(minx, miny, maxx, maxy, width, height)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width,
        count=3, dtype=arr.dtype, crs=crs, transform=transform,
    ) as dst:
        for band in range(3):
            dst.write(arr[:, :, band], band + 1)


def _write_geotiff_f32(
    arr: np.ndarray, path: str, bounds: tuple, crs: str,
) -> None:
    """Write single-band float32 array as GeoTIFF."""
    minx, miny, maxx, maxy = bounds
    height, width = arr.shape
    transform = from_bounds(minx, miny, maxx, maxy, width, height)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width,
        count=1, dtype="float32", crs=crs, transform=transform,
    ) as dst:
        dst.write(arr.astype(np.float32), 1)


def _inset_polygon(geom: Polygon, min_inset: float, max_inset: float,
                   rng: np.random.Generator) -> Polygon | None:
    """Return a smaller polygon inset from the parcel edges (building footprint)."""
    inset = rng.uniform(min_inset, max_inset)
    shrunk = geom.buffer(-inset)
    if shrunk.is_empty or shrunk.area < 10:
        return None
    if shrunk.geom_type == "MultiPolygon":
        shrunk = max(shrunk.geoms, key=lambda g: g.area)
    # Further reduce to simulate building not filling whole inset area
    scale_f = rng.uniform(0.3, 0.8)
    cx, cy = shrunk.centroid.x, shrunk.centroid.y
    from shapely.affinity import scale as shp_scale
    building = shp_scale(shrunk, xfact=scale_f, yfact=scale_f, origin=(cx, cy))
    if building.is_empty or building.area < 5:
        return None
    return building


# ---------------------------------------------------------------------------
# Public: render_synthetic_ori
# ---------------------------------------------------------------------------

def render_synthetic_ori(
    parcels: gpd.GeoDataFrame,
    output_path: str,
    gsd: float = 0.25,
    seed: int = 42,
) -> dict[str, Any]:
    """Render a synthetic ortho-rectified image (ORI) of the parcel fabric.

    Returns dict with keys: bounds, shape, path.
    """
    rng = np.random.default_rng(seed)
    bounds = _bounds_with_buffer(parcels)
    minx, miny, maxx, maxy = bounds
    width = int(math.ceil((maxx - minx) / gsd))
    height = int(math.ceil((maxy - miny) / gsd))

    # Background: dark green vegetation + noise
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :, 0] = 35  # R
    img[:, :, 1] = 80  # G
    img[:, :, 2] = 30  # B
    bg_noise = rng.integers(-10, 10, size=(height, width, 3), dtype=np.int16)
    img = np.clip(img.astype(np.int16) + bg_noise, 0, 255).astype(np.uint8)

    # Use PIL for polygon drawing
    pil_img = Image.fromarray(img, "RGB")
    draw = ImageDraw.Draw(pil_img)

    # Road corridors: draw grey between parcels
    # Simple approach: draw slightly expanded parcels in green, then the gap is road
    # Actually draw road as grey background first, then parcels on top
    # Redraw: fill whole thing grey first for roads, then parcels
    road_layer = Image.new("RGB", (width, height), (120, 115, 110))
    road_draw = ImageDraw.Draw(road_layer)

    # Draw parcel interiors on road layer (vegetation fill)
    for _, row in parcels.iterrows():
        px_coords = _geom_to_pixel_coords(row.geometry, minx, maxy, gsd)
        road_draw.polygon(px_coords, fill=(35 + rng.integers(-5, 5),
                                            80 + rng.integers(-5, 5),
                                            30 + rng.integers(-5, 5)))

    # Composite: road_layer is the base
    pil_img = road_layer

    # Add vegetation noise to parcel areas
    arr = np.array(pil_img)
    veg_noise = rng.integers(-8, 8, size=(height, width, 3), dtype=np.int16)
    arr = np.clip(arr.astype(np.int16) + veg_noise, 0, 255).astype(np.uint8)
    pil_img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(pil_img)

    # Roof color palette
    roof_colors = [
        (160, 80, 60),    # red-brown
        (140, 70, 50),    # darker red-brown
        (130, 130, 135),  # grey
        (110, 115, 130),  # blue-grey
        (150, 90, 70),    # terracotta
        (100, 100, 105),  # dark grey
    ]

    # Draw buildings and boundary walls
    for _, row in parcels.iterrows():
        geom = row.geometry

        # Building footprint
        building = _inset_polygon(geom, 3.0, 8.0, rng)
        if building is not None:
            bpx = _geom_to_pixel_coords(building, minx, maxy, gsd)
            color = roof_colors[rng.integers(0, len(roof_colors))]
            color_var = tuple(
                max(0, min(255, c + int(rng.integers(-15, 15)))) for c in color
            )
            draw.polygon(bpx, fill=color_var)

        # Boundary walls on ~60% of edges
        ext_coords = list(geom.exterior.coords)
        for i in range(len(ext_coords) - 1):
            if rng.random() < 0.6:
                p1 = _world_to_pixel(ext_coords[i][0], ext_coords[i][1], minx, maxy, gsd)
                p2 = _world_to_pixel(ext_coords[i + 1][0], ext_coords[i + 1][1], minx, maxy, gsd)
                wall_color = (40 + rng.integers(-10, 10),
                              35 + rng.integers(-10, 10),
                              30 + rng.integers(-10, 10))
                line_width = int(rng.integers(1, 3))
                draw.line([p1, p2], fill=wall_color, width=line_width)

    # Add Gaussian sensor noise
    final = np.array(pil_img).astype(np.int16)
    sensor_noise = rng.normal(0, 3, size=final.shape).astype(np.int16)
    final = np.clip(final + sensor_noise, 0, 255).astype(np.uint8)

    # Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    crs = str(parcels.crs) if parcels.crs else "EPSG:32643"

    if _HAS_RASTERIO:
        _write_geotiff_rgb(final, output_path, bounds, crs)
    else:
        Image.fromarray(final, "RGB").save(output_path)
        _write_world_file(output_path, minx, maxy, gsd)

    return {
        "bounds": list(bounds),
        "shape": [height, width],
        "path": str(output_path),
    }


# ---------------------------------------------------------------------------
# Public: render_synthetic_dsm_dtm
# ---------------------------------------------------------------------------

def render_synthetic_dsm_dtm(
    parcels: gpd.GeoDataFrame,
    dsm_path: str,
    dtm_path: str | None,
    gsd: float = 0.5,
    seed: int = 42,
) -> dict[str, Any]:
    """Render synthetic DSM and DTM rasters.

    DSM = flat terrain (100m) + building heights (5-12m).
    DTM = flat terrain (100m) only.
    If dtm_path is None, skip DTM generation (TC13).
    """
    rng = np.random.default_rng(seed)
    bounds = _bounds_with_buffer(parcels)
    minx, miny, maxx, maxy = bounds
    width = int(math.ceil((maxx - minx) / gsd))
    height = int(math.ceil((maxy - miny) / gsd))
    crs = str(parcels.crs) if parcels.crs else "EPSG:32643"

    # Base terrain at 100m with gentle undulation
    terrain = np.full((height, width), 100.0, dtype=np.float32)
    # Add mild terrain variation (< 1m)
    for i in range(height):
        for j in range(width):
            terrain[i, j] += 0.3 * np.sin(i / 50.0) + 0.2 * np.cos(j / 40.0)

    dtm_arr = terrain.copy()

    # DSM: add building heights
    dsm_arr = terrain.copy()
    pil_mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(pil_mask)

    building_heights: dict[int, float] = {}
    for idx, (_, row) in enumerate(parcels.iterrows()):
        building = _inset_polygon(row.geometry, 3.0, 8.0, rng)
        if building is None:
            continue
        bh = float(rng.uniform(5.0, 12.0))
        building_heights[idx] = bh
        bpx = _geom_to_pixel_coords(building, minx, maxy, gsd)
        # Create a fresh mask for this building
        b_mask = Image.new("L", (width, height), 0)
        b_draw = ImageDraw.Draw(b_mask)
        b_draw.polygon(bpx, fill=255)
        b_arr = np.array(b_mask)
        dsm_arr[b_arr > 0] += bh

    # Write DSM
    Path(dsm_path).parent.mkdir(parents=True, exist_ok=True)
    if _HAS_RASTERIO:
        _write_geotiff_f32(dsm_arr, dsm_path, bounds, crs)
    else:
        # Save as 16-bit TIFF (heights * 100 to preserve decimals)
        dsm_int = (dsm_arr * 100).astype(np.int32)
        Image.fromarray(dsm_int, mode="I").save(dsm_path)
        _write_world_file(dsm_path, minx, maxy, gsd)

    result: dict[str, Any] = {
        "dsm_path": str(dsm_path),
        "bounds": list(bounds),
        "shape": [height, width],
        "gsd": gsd,
    }

    # Write DTM (if requested)
    if dtm_path is not None:
        Path(dtm_path).parent.mkdir(parents=True, exist_ok=True)
        if _HAS_RASTERIO:
            _write_geotiff_f32(dtm_arr, dtm_path, bounds, crs)
        else:
            dtm_int = (dtm_arr * 100).astype(np.int32)
            Image.fromarray(dtm_int, mode="I").save(dtm_path)
            _write_world_file(dtm_path, minx, maxy, gsd)
        result["dtm_path"] = str(dtm_path)

    return result
