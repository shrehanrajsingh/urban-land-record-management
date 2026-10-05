"""CRS normalization helpers."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry


def ensure_crs(gdf: gpd.GeoDataFrame, target_crs: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        gdf = gdf.set_crs(target_crs)
    elif CRS.from_user_input(gdf.crs) != CRS.from_user_input(target_crs):
        gdf = gdf.to_crs(target_crs)
    return gdf


def validate_geometry(geom: BaseGeometry) -> tuple[bool, str]:
    if geom is None or geom.is_empty:
        return False, "empty_geometry"
    if not geom.is_valid:
        fixed = geom.buffer(0)
        if fixed.is_valid and not fixed.is_empty:
            return True, "fixed_with_buffer0"
        return False, "invalid_geometry"
    return True, "ok"


def geometry_metadata(geom: BaseGeometry) -> dict[str, Any]:
    return {
        "area": float(geom.area) if hasattr(geom, "area") else None,
        "bounds": list(geom.bounds),
        "geom_type": geom.geom_type,
        "is_valid": bool(geom.is_valid),
    }
