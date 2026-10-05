"""Dataset ingestion and CRS/schema normalization."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from geoalchemy2.elements import WKTElement
from sqlalchemy.orm import Session

from backend.models.schema import Dataset, Parcel, ParcelVersion
from backend.services.ids import new_id
from geospatial.crs.normalize import ensure_crs, validate_geometry
from geospatial.topology.validate import auto_fix_geometry


def ingest_geodataframe(
    db: Session,
    gdf: gpd.GeoDataFrame,
    *,
    dataset_id: str,
    name: str,
    source_type: str,
    target_crs: str = "EPSG:32643",
    status: str = "OBSERVED",
    created_by: str = "SYSTEM",
    created_by_role: str = "SYSTEM",
) -> dict[str, Any]:
    gdf = ensure_crs(gdf, target_crs)
    ds = Dataset(
        id=dataset_id,
        name=name,
        source_type=source_type,
        crs=target_crs,
        metadata_json={"n_features": len(gdf)},
    )
    db.merge(ds)

    created = 0
    for _, row in gdf.iterrows():
        geom = row.geometry
        ok, reason = validate_geometry(geom)
        if not ok:
            geom, _ = auto_fix_geometry(geom)
        parcel_id = str(row.get("parcel_id") or new_id("P"))
        if db.get(Parcel, parcel_id) is None:
            db.add(Parcel(id=parcel_id, external_id=str(row.get("survey_no", ""))))
        attrs = {
            k: (None if v != v else v)  # NaN check
            for k, v in row.drop(labels="geometry").items()
            if k not in ("parcel_id",)
        }
        # convert numpy types
        clean_attrs = {}
        for k, v in attrs.items():
            if hasattr(v, "item"):
                clean_attrs[k] = v.item()
            else:
                clean_attrs[k] = v

        pv_id = new_id("PV")
        unc = row["uncertainty_m"] if "uncertainty_m" in row.index else 0.25
        db.add(
            ParcelVersion(
                id=pv_id,
                parcel_id=parcel_id,
                version=1,
                geometry=WKTElement(geom.wkt, srid=32643),
                status=status,
                area=float(geom.area),
                attributes=clean_attrs,
                source_ids=[dataset_id],
                geometry_uncertainty_m=float(unc or 0.25),
                created_by=created_by,
                created_by_role=created_by_role,
            )
        )
        created += 1

    db.commit()
    return {"dataset_id": dataset_id, "parcels_created": created}
