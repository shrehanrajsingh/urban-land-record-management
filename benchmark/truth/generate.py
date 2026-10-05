"""Generate a clean synthetic cadastral parcel fabric (truth layer)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, Polygon, box


@dataclass
class TruthParcel:
    parcel_id: str
    geometry: Polygon
    attributes: dict[str, Any]


def generate_grid_fabric(
    origin: tuple[float, float] = (500000.0, 3000000.0),
    rows: int = 5,
    cols: int = 5,
    cell_w: float = 40.0,
    cell_h: float = 30.0,
    gap: float = 0.0,
    crs: str = "EPSG:32643",
    seed: int = 42,
) -> gpd.GeoDataFrame:
    """Create a regular urban block parcel fabric with optional irregular parcels."""
    rng = np.random.default_rng(seed)
    ox, oy = origin
    records = []
    pid = 1
    for r in range(rows):
        for c in range(cols):
            x0 = ox + c * (cell_w + gap)
            y0 = oy + r * (cell_h + gap)
            # slight irregularity for realism
            jitter = rng.uniform(-0.5, 0.5, size=(4, 2))
            poly = Polygon(
                [
                    (x0 + jitter[0, 0], y0 + jitter[0, 1]),
                    (x0 + cell_w + jitter[1, 0], y0 + jitter[1, 1]),
                    (x0 + cell_w + jitter[2, 0], y0 + cell_h + jitter[2, 1]),
                    (x0 + jitter[3, 0], y0 + cell_h + jitter[3, 1]),
                ]
            )
            parcel_id = f"P-{pid:03d}"
            records.append(
                {
                    "parcel_id": parcel_id,
                    "survey_no": f"SN-{100 + pid}",
                    "owner_name": f"Owner_{pid}",
                    "land_use": rng.choice(["residential", "commercial", "mixed"]),
                    "revenue_area": round(float(poly.area), 2),
                    "geometry": poly,
                }
            )
            pid += 1

    # Add one intentionally larger corner parcel for merge demos
    big = box(ox + cols * (cell_w + gap) + 5, oy, ox + cols * (cell_w + gap) + 5 + cell_w * 1.5, oy + cell_h)
    records.append(
        {
            "parcel_id": f"P-{pid:03d}",
            "survey_no": f"SN-{100 + pid}",
            "owner_name": f"Owner_{pid}",
            "land_use": "residential",
            "revenue_area": round(float(big.area), 2),
            "geometry": big,
        }
    )

    gdf = gpd.GeoDataFrame(records, crs=crs)
    return gdf


def generate_control_points(gdf: gpd.GeoDataFrame, n: int = 12, seed: int = 7) -> gpd.GeoDataFrame:
    rng = np.random.default_rng(seed)
    pts = []
    bounds = gdf.total_bounds
    for i in range(n):
        # sample parcel corners / centroids
        row = gdf.iloc[int(rng.integers(0, len(gdf)))]
        geom = row.geometry
        if rng.random() < 0.5:
            coords = list(geom.exterior.coords)[:-1]
            c = coords[int(rng.integers(0, len(coords)))]
        else:
            c = (geom.centroid.x, geom.centroid.y)
        # assign region by quadrant
        mx = (bounds[0] + bounds[2]) / 2
        my = (bounds[1] + bounds[3]) / 2
        region = ("NW" if c[1] >= my else "SW") + ("E" if c[0] >= mx else "W")
        # normalize region labels NW/NE/SW/SE
        region = ("N" if c[1] >= my else "S") + ("E" if c[0] >= mx else "W")
        pts.append(
            {
                "id": f"CP-{i+1:02d}",
                "region": region,
                "geometry": Point(c),
            }
        )
    return gpd.GeoDataFrame(pts, crs=gdf.crs)


def generate_gnss_boundary_points(gdf: gpd.GeoDataFrame, seed: int = 11) -> gpd.GeoDataFrame:
    rng = np.random.default_rng(seed)
    pts = []
    for _, row in gdf.iterrows():
        coords = list(row.geometry.exterior.coords)[:-1]
        for i, c in enumerate(coords):
            if rng.random() < 0.35:
                noise = rng.normal(0, 0.05, size=2)
                pts.append(
                    {
                        "id": f"GNSS-{row.parcel_id}-{i}",
                        "parcel_id": row.parcel_id,
                        "uncertainty_m": 0.05,
                        "geometry": Point(c[0] + noise[0], c[1] + noise[1]),
                    }
                )
    return gpd.GeoDataFrame(pts, crs=gdf.crs)
