"""Inject known conflicts into a parcel fabric for evaluation."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from shapely.affinity import translate


def inject_overlap(gdf: gpd.GeoDataFrame, parcel_id: str, dx: float = 5.0) -> gpd.GeoDataFrame:
    out = gdf.copy()
    mask = out["parcel_id"] == parcel_id
    out.loc[mask, "geometry"] = out.loc[mask, "geometry"].apply(lambda g: translate(g, xoff=dx))
    return out


def inject_gap(gdf: gpd.GeoDataFrame, parcel_id: str, shrink: float = 2.0) -> gpd.GeoDataFrame:
    out = gdf.copy()
    mask = out["parcel_id"] == parcel_id
    out.loc[mask, "geometry"] = out.loc[mask, "geometry"].buffer(-shrink)
    return out


def inject_attribute_mismatch(gdf: gpd.GeoDataFrame, parcel_id: str) -> gpd.GeoDataFrame:
    out = gdf.copy()
    mask = out["parcel_id"] == parcel_id
    out.loc[mask, "revenue_area"] = out.loc[mask, "revenue_area"] * 1.25
    out.loc[mask, "survey_no"] = "CONFLICT-ID"
    return out


def conflict_catalog() -> list[dict[str, Any]]:
    return [
        {"id": "C-OVERLAP", "type": "PARCEL_OVERLAP", "parcel_id": "P-001"},
        {"id": "C-GAP", "type": "PARCEL_GAP", "parcel_id": "P-002"},
        {"id": "C-ATTR", "type": "ATTRIBUTE_CONFLICT", "parcel_id": "P-103"},
        {"id": "C-REG", "type": "REGISTRATION_FAILURE", "parcel_id": None},
        {"id": "C-AMB", "type": "AMBIGUOUS_MATCH", "parcel_id": "P-010"},
    ]
