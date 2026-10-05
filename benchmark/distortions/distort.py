"""Distort a truth fabric into a synthetic legacy cadastral layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
from shapely.affinity import affine_transform, rotate, scale, translate
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry


@dataclass
class DistortionSpec:
    translation: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = 0.0
    scale: float = 1.0
    shear: float = 0.0
    local_distortion: bool = False
    vertex_noise: float = 0.0
    missing_vertices: bool = False
    small_gaps: bool = False
    small_overlaps: bool = False
    split_parcels: list[str] | None = None
    merge_parcels: list[list[str]] | None = None
    attribute_corruption: bool = False
    seed: int = 42


def _shear_matrix(k: float) -> list[float]:
    # [a, b, d, e, xoff, yoff]
    return [1, k, 0, 1, 0, 0]


def _local_warp(geom: BaseGeometry, rng: np.random.Generator, amplitude: float = 0.8) -> BaseGeometry:
    if geom.geom_type != "Polygon":
        return geom
    cx, cy = geom.centroid.x, geom.centroid.y
    coords = []
    for x, y in geom.exterior.coords:
        dx = amplitude * np.sin((x - cx) / 20.0)
        dy = amplitude * np.cos((y - cy) / 15.0)
        coords.append((x + dx, y + dy))
    return Polygon(coords)


def _vertex_noise(geom: BaseGeometry, sigma: float, rng: np.random.Generator) -> BaseGeometry:
    if geom.geom_type != "Polygon" or sigma <= 0:
        return geom
    coords = []
    for i, (x, y) in enumerate(geom.exterior.coords):
        if i == len(list(geom.exterior.coords)) - 1:
            coords.append(coords[0])
            break
        coords.append((x + rng.normal(0, sigma), y + rng.normal(0, sigma)))
    return Polygon(coords)


def _drop_vertex(geom: BaseGeometry, rng: np.random.Generator) -> BaseGeometry:
    if geom.geom_type != "Polygon":
        return geom
    coords = list(geom.exterior.coords)[:-1]
    if len(coords) <= 4:
        return geom
    idx = int(rng.integers(1, len(coords) - 1))
    coords.pop(idx)
    return Polygon(coords)


def distort(
    truth: gpd.GeoDataFrame,
    spec: DistortionSpec | None = None,
    **kwargs: Any,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """
    Apply whole-layer then local distortions. Returns (legacy_gdf, applied_params).
    """
    if spec is None:
        spec = DistortionSpec(**kwargs)
    rng = np.random.default_rng(spec.seed)
    gdf = truth.copy()
    origin = (gdf.union_all() if hasattr(gdf, "union_all") else gdf.unary_union).centroid

    # Whole-layer similarity + shear
    geoms = []
    for geom in gdf.geometry:
        g = translate(geom, xoff=spec.translation[0], yoff=spec.translation[1])
        g = rotate(g, spec.rotation_deg, origin=origin)
        g = scale(g, xfact=spec.scale, yfact=spec.scale, origin=origin)
        if spec.shear:
            g = affine_transform(g, _shear_matrix(spec.shear))
        if spec.local_distortion:
            g = _local_warp(g, rng)
        if spec.vertex_noise:
            g = _vertex_noise(g, spec.vertex_noise, rng)
        if spec.missing_vertices and rng.random() < 0.3:
            g = _drop_vertex(g, rng)
        geoms.append(g)
    gdf = gdf.set_geometry(geoms)

    # Structural: splits
    records = gdf.to_dict("records")
    if spec.split_parcels:
        new_records = []
        for rec in records:
            if rec["parcel_id"] in spec.split_parcels:
                geom: Polygon = rec["geometry"]
                minx, miny, maxx, maxy = geom.bounds
                mid = (minx + maxx) / 2
                left = geom.intersection(Polygon([(minx - 1, miny - 1), (mid, miny - 1), (mid, maxy + 1), (minx - 1, maxy + 1)]))
                right = geom.intersection(Polygon([(mid, miny - 1), (maxx + 1, miny - 1), (maxx + 1, maxy + 1), (mid, maxy + 1)]))
                for i, part in enumerate([left, right]):
                    if part.is_empty:
                        continue
                    if part.geom_type == "MultiPolygon":
                        part = max(part.geoms, key=lambda x: x.area)
                    r = dict(rec)
                    r["parcel_id"] = f"{rec['parcel_id']}-S{i+1}"
                    r["survey_no"] = f"{rec['survey_no']}-S{i+1}"
                    r["geometry"] = part
                    r["revenue_area"] = round(float(part.area), 2)
                    r["_parent_id"] = rec["parcel_id"]
                    new_records.append(r)
            else:
                new_records.append(rec)
        records = new_records

    # Structural: merges (collapse listed groups into one)
    if spec.merge_parcels:
        merge_lookup = {}
        for group in spec.merge_parcels:
            for pid in group:
                merge_lookup[pid] = tuple(group)
        kept = []
        seen = set()
        for rec in records:
            key = merge_lookup.get(rec["parcel_id"])
            if not key:
                kept.append(rec)
                continue
            if key in seen:
                continue
            seen.add(key)
            members = [r for r in records if r["parcel_id"] in key]
            from shapely.ops import unary_union

            u = unary_union([m["geometry"] for m in members])
            base = dict(members[0])
            base["parcel_id"] = "M-" + "-".join(key)
            base["survey_no"] = "MERGED"
            base["geometry"] = u if u.geom_type == "Polygon" else max(u.geoms, key=lambda x: x.area)
            base["revenue_area"] = round(float(base["geometry"].area), 2)
            base["_merged_from"] = list(key)
            kept.append(base)
        records = kept

    # Attribute corruption
    if spec.attribute_corruption:
        for rec in records:
            if rng.random() < 0.25:
                rec["revenue_area"] = round(float(rec["revenue_area"]) * rng.uniform(0.7, 1.3), 2)
            if rng.random() < 0.15:
                rec["survey_no"] = rec["survey_no"] + "_X"

    # Case B fixture: specific attribute conflict on P-103 if present
    for rec in records:
        if rec.get("parcel_id") in ("P-103", "P-103-S1", "P-103-S2") or str(rec.get("parcel_id", "")).startswith("P-103"):
            if rec["parcel_id"] == "P-103":
                rec["revenue_area"] = round(float(rec.get("revenue_area", 100)) * 1.19, 2)
                rec["survey_no"] = "SN-AMBIG"

    out = gpd.GeoDataFrame(records, crs=truth.crs)
    params = {
        "translation": spec.translation,
        "rotation_deg": spec.rotation_deg,
        "scale": spec.scale,
        "shear": spec.shear,
        "local_distortion": spec.local_distortion,
        "vertex_noise": spec.vertex_noise,
        "split_parcels": spec.split_parcels,
        "merge_parcels": spec.merge_parcels,
        "attribute_corruption": spec.attribute_corruption,
        "seed": spec.seed,
    }
    return out, params


def inverse_control_from_distortion(
    control: gpd.GeoDataFrame,
    translation: tuple[float, float],
    rotation_deg: float,
    scale_factor: float,
    origin_xy: tuple[float, float],
) -> gpd.GeoDataFrame:
    """Map truth control points into distorted (legacy) space for registration tests."""
    pts = []
    for _, row in control.iterrows():
        g = row.geometry
        g = translate(g, xoff=translation[0], yoff=translation[1])
        g = rotate(g, rotation_deg, origin=origin_xy)
        g = scale(g, xfact=scale_factor, yfact=scale_factor, origin=origin_xy)
        pts.append({**row.drop(labels="geometry"), "geometry": g})
    return gpd.GeoDataFrame(pts, crs=control.crs)
