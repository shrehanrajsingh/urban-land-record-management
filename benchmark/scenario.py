"""Deterministic scenario generator for cadastral benchmark evaluation.

Produces a truth fabric, a new survey reference layer (SURVEY-2026), a legacy
cadastre (CAD-1998), GNSS points, control-point pairs, and ground-truth
correspondence / conflict metadata.
"""

from __future__ import annotations

import json
from typing import Any

import geopandas as gpd
import numpy as np
from shapely.affinity import rotate, scale, translate
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ORIGIN = (500000.0, 3000000.0)
_CELL_W = 40.0
_CELL_H = 30.0
_ROAD_GAP = 5.0
_CRS = "EPSG:32643"
_ROWS = 5
_COLS = 6

# Distortion parameters for legacy layer
_TRANSLATION = (1.2, -0.7)
_ROTATION_DEG = 0.8
_SCALE_FACTOR = 1.002
_VERTEX_NOISE_LEGACY = 0.15
_VERTEX_NOISE_SURVEY = 0.05

# Special-case parcel IDs
_SPLIT_SRC = "P-104"
_SPLIT_CHILDREN = ("P-202", "P-203")
_MERGE_SRCS = ("P-020", "P-021")
_MERGE_CHILD = "P-230"
_CASE_B_PARCEL = "P-103"
_CASE_A_PARCEL = "P-015"
_GAP_PARCEL = "P-008"
_OVERLAP_PARCEL = "P-001"
_NO_MATCH_PARCEL = "P-026"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_irregular_quad(
    x0: float, y0: float, w: float, h: float, rng: np.random.Generator
) -> Polygon:
    """Create an irregular quadrilateral with slight vertex jitter."""
    jitter = rng.uniform(-0.5, 0.5, size=(4, 2))
    return Polygon([
        (x0 + jitter[0, 0], y0 + jitter[0, 1]),
        (x0 + w + jitter[1, 0], y0 + jitter[1, 1]),
        (x0 + w + jitter[2, 0], y0 + h + jitter[2, 1]),
        (x0 + jitter[3, 0], y0 + h + jitter[3, 1]),
    ])


def _vertex_noise(geom: Polygon, sigma: float, rng: np.random.Generator) -> Polygon:
    """Add Gaussian noise to each vertex."""
    if sigma <= 0:
        return geom
    coords = list(geom.exterior.coords)[:-1]
    noisy = [(x + rng.normal(0, sigma), y + rng.normal(0, sigma)) for x, y in coords]
    return Polygon(noisy)


def _warp_geometry(geom: Polygon, bounds: tuple, rng: np.random.Generator,
                   max_amplitude: float = 1.5) -> Polygon:
    """Apply the global sinusoidal warp field to every vertex."""
    coords = list(geom.exterior.coords)[:-1]
    warped = []
    for x, y in coords:
        dx, dy = _global_warp_field(x, y, bounds, rng, max_amplitude)
        warped.append((x + dx, y + dy))
    return Polygon(warped)


def _global_warp_field(
    x: float, y: float, bounds: tuple, rng: np.random.Generator,
    max_amplitude: float = 1.5,
) -> tuple[float, float]:
    """Smooth sinusoidal displacement field, stronger toward SE corner."""
    nx = (x - bounds[0]) / max(bounds[2] - bounds[0], 1e-9)
    ny = (y - bounds[1]) / max(bounds[3] - bounds[1], 1e-9)
    amp = 0.3 + (max_amplitude - 0.3) * (nx * 0.5 + (1 - ny) * 0.5)
    dx = amp * np.sin(2 * np.pi * nx * 1.5)
    dy = amp * np.cos(2 * np.pi * ny * 1.2)
    return float(dx), float(dy)


def _corrupt_attributes(rec: dict, rng: np.random.Generator) -> dict:
    """Randomly corrupt ~25% of parcel attributes."""
    rec = dict(rec)
    if rng.random() < 0.25:
        rec["revenue_area"] = round(float(rec["revenue_area"]) * rng.uniform(0.7, 1.3), 2)
    if rng.random() < 0.15:
        rec["survey_no"] = rec["survey_no"] + "_X"
    if rng.random() < 0.10:
        rec["owner_name"] = "UNKNOWN"
    return rec


# ---------------------------------------------------------------------------
# Truth fabric
# ---------------------------------------------------------------------------

def _generate_truth(rng: np.random.Generator) -> gpd.GeoDataFrame:
    """~6x5 grid of 30 parcels with 5m road corridors."""
    ox, oy = _ORIGIN
    records: list[dict[str, Any]] = []
    pid = 1
    land_uses = ["residential", "commercial", "mixed", "agricultural"]
    for r in range(_ROWS):
        for c in range(_COLS):
            x0 = ox + c * (_CELL_W + _ROAD_GAP)
            y0 = oy + r * (_CELL_H + _ROAD_GAP)
            poly = _make_irregular_quad(x0, y0, _CELL_W, _CELL_H, rng)
            parcel_id = f"P-{pid:03d}"
            records.append({
                "parcel_id": parcel_id,
                "survey_no": f"SN-{100 + pid}",
                "owner_name": f"Owner_{pid}",
                "land_use": str(rng.choice(land_uses)),
                "revenue_area": round(float(poly.area), 2),
                "geometry": poly,
            })
            pid += 1
    gdf = gpd.GeoDataFrame(records, crs=_CRS)

    # Rename specific parcels to match expected IDs for special cases
    _renames = {"P-013": "P-103", "P-014": "P-104"}
    for old_id, new_pid in _renames.items():
        idx = gdf.index[gdf["parcel_id"] == old_id]
        if len(idx):
            i = idx[0]
            gdf.at[i, "parcel_id"] = new_pid
            gdf.at[i, "survey_no"] = f"SN-{int(new_pid[2:]) + 100}"

    return gdf


# ---------------------------------------------------------------------------
# Survey layer  (SURVEY-2026)
# ---------------------------------------------------------------------------

def _generate_survey(truth: gpd.GeoDataFrame, rng: np.random.Generator) -> tuple[
    gpd.GeoDataFrame, list[dict]
]:
    """Build survey from truth with split, merge, low noise, and ID rename."""
    records = truth.to_dict("records")
    correspondences: list[dict] = []

    # ---- Split P-104 -> P-202, P-203 (vertical split) --------------------
    new_records: list[dict] = []
    for rec in records:
        if rec["parcel_id"] == _SPLIT_SRC:
            geom: Polygon = rec["geometry"]
            minx, miny, maxx, maxy = geom.bounds
            mid_x = (minx + maxx) / 2
            left = geom.intersection(
                Polygon([(minx - 1, miny - 1), (mid_x, miny - 1),
                          (mid_x, maxy + 1), (minx - 1, maxy + 1)])
            )
            right = geom.intersection(
                Polygon([(mid_x, miny - 1), (maxx + 1, miny - 1),
                          (maxx + 1, maxy + 1), (mid_x, maxy + 1)])
            )
            for child_id, part in zip(_SPLIT_CHILDREN, [left, right]):
                if part.is_empty:
                    continue
                if part.geom_type == "MultiPolygon":
                    part = max(part.geoms, key=lambda g: g.area)
                r = dict(rec)
                r["parcel_id"] = child_id
                r["survey_no"] = f"SN-{child_id[2:]}"
                r["revenue_area"] = round(float(part.area), 2)
                r["geometry"] = part
                new_records.append(r)
            correspondences.append({
                "legacy_id": _SPLIT_SRC,
                "reference_ids": list(_SPLIT_CHILDREN),
                "relation_type": "SPLIT",
            })
        else:
            new_records.append(rec)
    records = new_records

    # ---- Merge P-020 + P-021 -> P-230 ------------------------------------
    merge_set = set(_MERGE_SRCS)
    merge_members = [r for r in records if r["parcel_id"] in merge_set]
    non_merge = [r for r in records if r["parcel_id"] not in merge_set]
    if len(merge_members) == 2:
        merged_geom = unary_union([m["geometry"] for m in merge_members])
        if merged_geom.geom_type == "MultiPolygon":
            merged_geom = max(merged_geom.geoms, key=lambda g: g.area)
        mr = dict(merge_members[0])
        mr["parcel_id"] = _MERGE_CHILD
        mr["survey_no"] = f"SN-{_MERGE_CHILD[2:]}"
        mr["revenue_area"] = round(float(merged_geom.area), 2)
        mr["geometry"] = merged_geom
        non_merge.append(mr)
        for src in _MERGE_SRCS:
            correspondences.append({
                "legacy_id": src,
                "reference_ids": [_MERGE_CHILD],
                "relation_type": "MERGE",
            })
    records = non_merge

    # ---- Rename to survey IDs (S-XXX) and add noise ----------------------
    survey_records: list[dict] = []
    for rec in records:
        rec = dict(rec)
        old_id = rec["parcel_id"]
        # already renamed parcels (P-202, P-203, P-230) keep their IDs as survey IDs
        if old_id.startswith("P-"):
            num = old_id[2:]
            survey_id = f"S-{num}"
        else:
            survey_id = old_id  # P-202 etc already handled above
        # If it's a split/merge child, survey_id is already set above in correspondences
        if old_id in (_MERGE_CHILD, *_SPLIT_CHILDREN):
            survey_id = f"S-{old_id[2:]}"

        rec["parcel_id"] = survey_id
        rec["geometry"] = _vertex_noise(rec["geometry"], _VERTEX_NOISE_SURVEY, rng)
        survey_records.append(rec)

        # Add ONE_TO_ONE correspondences for normal parcels
        if old_id not in merge_set and old_id != _SPLIT_SRC and old_id not in _SPLIT_CHILDREN and old_id != _MERGE_CHILD:
            correspondences.append({
                "legacy_id": old_id,
                "reference_ids": [survey_id],
                "relation_type": "ONE_TO_ONE",
            })

    gdf = gpd.GeoDataFrame(survey_records, crs=_CRS)
    return gdf, correspondences


# ---------------------------------------------------------------------------
# Legacy cadastre  (CAD-1998)
# ---------------------------------------------------------------------------

def _apply_affine_to_geom(
    geom: Polygon, tx: float, ty: float, rot_deg: float,
    sc: float, origin_pt: tuple[float, float],
) -> Polygon:
    """Translation → Rotation → Scale (matching the old distortion pipeline)."""
    g = translate(geom, xoff=tx, yoff=ty)
    g = rotate(g, rot_deg, origin=origin_pt)
    g = scale(g, xfact=sc, yfact=sc, origin=origin_pt)
    return g


def _generate_legacy(
    truth: gpd.GeoDataFrame, rng: np.random.Generator
) -> tuple[gpd.GeoDataFrame, dict, list[dict]]:
    """Create legacy layer from truth with affine + warp + noise + corruption."""
    # Compute centroid of truth for rotation/scale origin
    union = unary_union(truth.geometry.values)
    origin_pt = (union.centroid.x, union.centroid.y)

    # First apply affine to all geometries so we can compute warp bounds
    affine_geoms = []
    for geom in truth.geometry:
        affine_geoms.append(
            _apply_affine_to_geom(geom, *_TRANSLATION, _ROTATION_DEG, _SCALE_FACTOR, origin_pt)
        )

    # Compute bounds of the affine-transformed layer for warp field normalization
    tmp = gpd.GeoDataFrame(geometry=affine_geoms, crs=_CRS)
    warp_bounds = tuple(tmp.total_bounds)  # (minx, miny, maxx, maxy)

    # Now apply warp + noise to each geometry
    records: list[dict] = []
    injected_conflicts: list[dict] = []

    for idx, (_, row) in enumerate(truth.iterrows()):
        rec = row.to_dict()
        geom = affine_geoms[idx]
        pid = rec["parcel_id"]

        # Global warp
        geom = _warp_geometry(geom, warp_bounds, rng)

        # Vertex noise
        geom = _vertex_noise(geom, _VERTEX_NOISE_LEGACY, rng)

        # --- Special cases ---
        # Case B: P-103 attribute conflict
        if pid == _CASE_B_PARCEL:
            rec["revenue_area"] = round(float(rec["revenue_area"]) * 1.19, 2)
            rec["survey_no"] = "SN-AMBIG"
            injected_conflicts.append({
                "parcel_id": pid,
                "conflict_type": "CASE_B_ATTRIBUTE",
                "description": "revenue_area +19% and survey_no = SN-AMBIG in legacy",
            })

        # Case A: P-015 extra displacement
        if pid == _CASE_A_PARCEL:
            geom = translate(geom, xoff=4.0, yoff=0.0)
            injected_conflicts.append({
                "parcel_id": pid,
                "conflict_type": "CASE_A_DISPLACEMENT",
                "description": "Additional 4m displacement applied to this parcel",
            })

        # Gap: P-008 shrunk by 2m buffer
        if pid == _GAP_PARCEL:
            shrunk = geom.buffer(-2.0)
            if not shrunk.is_empty and shrunk.area > 0:
                if shrunk.geom_type == "MultiPolygon":
                    shrunk = max(shrunk.geoms, key=lambda g: g.area)
                geom = shrunk
            injected_conflicts.append({
                "parcel_id": pid,
                "conflict_type": "GAP",
                "description": "Parcel shrunk by 2m buffer creating gap in legacy",
            })

        # Overlap: P-001 shifted 5m right
        if pid == _OVERLAP_PARCEL:
            geom = translate(geom, xoff=5.0, yoff=0.0)
            injected_conflicts.append({
                "parcel_id": pid,
                "conflict_type": "OVERLAP",
                "description": "Parcel shifted 5m right in legacy creating overlap",
            })

        rec["geometry"] = geom

        # Attribute corruption (~25%)
        if pid not in (_CASE_B_PARCEL,):
            rec = _corrupt_attributes(rec, rng)

        records.append(rec)

    # No-match: remove P-026 from legacy
    records = [r for r in records if r["parcel_id"] != _NO_MATCH_PARCEL]
    injected_conflicts.append({
        "parcel_id": _NO_MATCH_PARCEL,
        "conflict_type": "NO_MATCH",
        "description": "Parcel exists in truth but missing from legacy",
    })

    # Prefix legacy IDs with L- to avoid DB collisions
    for rec in records:
        rec["parcel_id"] = f"L-{rec['parcel_id']}"

    gdf = gpd.GeoDataFrame(records, crs=_CRS)

    distortion_params = {
        "translation": list(_TRANSLATION),
        "rotation_deg": _ROTATION_DEG,
        "scale": _SCALE_FACTOR,
        "vertex_noise": _VERTEX_NOISE_LEGACY,
        "warp_type": "sinusoidal_global",
        "warp_max_amplitude": 1.5,
        "warp_min_amplitude": 0.3,
        "rotation_origin": list(origin_pt),
    }

    return gdf, distortion_params, injected_conflicts


# ---------------------------------------------------------------------------
# GNSS points
# ---------------------------------------------------------------------------

def _generate_gnss(survey: gpd.GeoDataFrame, rng: np.random.Generator) -> gpd.GeoDataFrame:
    """GNSS points on survey corners with σ = 0.05m."""
    pts: list[dict] = []
    for _, row in survey.iterrows():
        coords = list(row.geometry.exterior.coords)[:-1]
        for i, (x, y) in enumerate(coords):
            noise = rng.normal(0, 0.05, size=2)
            pts.append({
                "id": f"GNSS-{row['parcel_id']}-{i}",
                "parcel_id": row["parcel_id"],
                "uncertainty_m": 0.05,
                "geometry": Point(x + noise[0], y + noise[1]),
            })
    return gpd.GeoDataFrame(pts, crs=_CRS)


# ---------------------------------------------------------------------------
# Control-point pairs
# ---------------------------------------------------------------------------

def _compute_control_point_pairs(
    truth: gpd.GeoDataFrame,
    legacy: gpd.GeoDataFrame,
    rng: np.random.Generator,
    n: int = 16,
) -> list[dict]:
    """Compute control-point pairs by matching corners truth→legacy.

    We pick corners from truth parcels, apply the known affine + warp to
    compute the corresponding legacy-space coordinate, then record both.
    """
    union = unary_union(truth.geometry.values)
    origin_pt = (union.centroid.x, union.centroid.y)

    # Affine-then-warp bounds (recompute for consistency)
    affine_geoms = [
        _apply_affine_to_geom(g, *_TRANSLATION, _ROTATION_DEG, _SCALE_FACTOR, origin_pt)
        for g in truth.geometry
    ]
    tmp = gpd.GeoDataFrame(geometry=affine_geoms, crs=_CRS)
    warp_bounds = tuple(tmp.total_bounds)

    # Gather all truth corners
    all_corners: list[tuple[float, float, str, str]] = []
    truth_bounds = truth.total_bounds
    mx = (truth_bounds[0] + truth_bounds[2]) / 2
    my = (truth_bounds[1] + truth_bounds[3]) / 2

    for _, row in truth.iterrows():
        pid = row["parcel_id"]
        if pid == _NO_MATCH_PARCEL:
            continue
        for x, y in list(row.geometry.exterior.coords)[:-1]:
            region = ("N" if y >= my else "S") + ("E" if x >= mx else "W")
            all_corners.append((x, y, pid, region))

    # Sample n corners
    indices = rng.choice(len(all_corners), size=min(n, len(all_corners)), replace=False)
    pairs: list[dict] = []
    for idx in indices:
        x, y, pid, region = all_corners[idx]
        # Target = truth coordinate
        target = [float(x), float(y)]
        # Source = legacy-space coordinate (affine + warp)
        pt = Point(x, y)
        pt_aff = translate(pt, xoff=_TRANSLATION[0], yoff=_TRANSLATION[1])
        pt_aff = rotate(pt_aff, _ROTATION_DEG, origin=origin_pt)
        pt_aff = scale(pt_aff, xfact=_SCALE_FACTOR, yfact=_SCALE_FACTOR, origin=origin_pt)
        dx, dy = _global_warp_field(pt_aff.x, pt_aff.y, warp_bounds, rng)
        source = [float(pt_aff.x + dx), float(pt_aff.y + dy)]
        pairs.append({
            "id": f"CP-{len(pairs)+1:02d}",
            "region": region,
            "source": source,
            "target": target,
        })

    return pairs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_scenario(seed: int = 42) -> dict:
    """Generate the full benchmark scenario deterministically.

    Returns
    -------
    dict with keys:
        truth      – GeoDataFrame (30 parcels, clean)
        survey     – GeoDataFrame (SURVEY-2026 with split/merge/noise)
        legacy     – GeoDataFrame (CAD-1998 with distortions)
        gnss       – GeoDataFrame (GNSS boundary points)
        ground_truth – dict with correspondences, conflicts, params
        control_points – list[dict] with source/target pairs
    """
    rng = np.random.default_rng(seed)

    # 1. Truth fabric
    truth = _generate_truth(rng)

    # 2. Survey layer (from truth, with structural changes + low noise)
    survey, correspondences = _generate_survey(truth, rng)

    # Add NO_MATCH correspondence for P-026
    correspondences.append({
        "legacy_id": _NO_MATCH_PARCEL,
        "reference_ids": [],
        "relation_type": "NO_MATCH",
    })

    # 3. Legacy cadastre (from truth *before* split/merge = old state)
    legacy, distortion_params, injected_conflicts = _generate_legacy(truth, rng)

    # 4. GNSS on survey corners
    gnss = _generate_gnss(survey, rng)

    # 5. Control-point pairs
    control_points = _compute_control_point_pairs(truth, legacy, rng)

    # 6. Ground truth bundle
    ground_truth = {
        "correspondences": correspondences,
        "injected_conflicts": injected_conflicts,
        "distortion_params": distortion_params,
        "control_point_pairs": control_points,
    }

    return {
        "truth": truth,
        "survey": survey,
        "legacy": legacy,
        "gnss": gnss,
        "ground_truth": ground_truth,
        "control_points": control_points,
    }
