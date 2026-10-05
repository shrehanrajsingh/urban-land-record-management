"""OGC API Features - Part 1 compliant collections and items router."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db

router = APIRouter(prefix="/ogc", tags=["OGC API Features"])

# Collection definitions mapped to database queries
COLLECTIONS = {
    "parcels": {
        "id": "parcels",
        "title": "Cadastral Parcels",
        "description": "Current parcel versions with geometry and attributes",
        "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]], "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "query": """
            SELECT DISTINCT ON (pv.parcel_id)
                pv.parcel_id AS id, pv.version, pv.status, pv.area,
                pv.attributes, pv.match_probability, pv.geometry_uncertainty_m,
                pv.source_ids, pv.created_at,
                ST_AsGeoJSON(ST_Transform(pv.geometry, 4326)) AS geom
            FROM parcel_versions pv
            ORDER BY pv.parcel_id, pv.version DESC
        """,
    },
    "observations": {
        "id": "observations",
        "title": "Physical Evidence Observations",
        "description": "Building footprints, walls, and other boundary evidence extracted from imagery",
        "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]], "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "query": """
            SELECT id, type, model_confidence, positional_uncertainty_m,
                   model, properties, source_dataset, created_at,
                   ST_AsGeoJSON(ST_Transform(geometry, 4326)) AS geom
            FROM observations
        """,
    },
    "survey_points": {
        "id": "survey_points",
        "title": "GNSS Survey Points",
        "description": "High-precision GNSS boundary survey points",
        "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]], "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "query": """
            SELECT id, point_type, uncertainty_m, properties,
                   source_dataset, 
                   ST_AsGeoJSON(ST_Transform(geometry, 4326)) AS geom
            FROM survey_points
        """,
    },
    "conflicts": {
        "id": "conflicts",
        "title": "Detected Conflicts",
        "description": "Geometric, attribute, and structural conflicts requiring resolution",
        "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]], "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "query": """
            SELECT id, conflict_type, severity, parcel_ids,
                   description, details, resolved, created_at,
                   CASE WHEN geometry IS NOT NULL
                        THEN ST_AsGeoJSON(ST_Transform(geometry, 4326))
                        ELSE NULL END AS geom
            FROM conflicts
        """,
    },
}


@router.get("/")
def landing_page(request: Request):
    """OGC API landing page."""
    base = str(request.base_url).rstrip("/")
    return {
        "title": "Cadastral Evidence Fusion - OGC API",
        "description": "OGC API Features access to cadastral parcels, observations, and conflicts",
        "links": [
            {"href": f"{base}/ogc/", "rel": "self", "type": "application/json", "title": "This document"},
            {"href": f"{base}/ogc/conformance", "rel": "conformance", "type": "application/json"},
            {"href": f"{base}/ogc/collections", "rel": "data", "type": "application/json"},
        ],
    }


@router.get("/conformance")
def conformance():
    """OGC API conformance declaration."""
    return {
        "conformsTo": [
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
        ]
    }


@router.get("/collections")
def list_collections(request: Request):
    """List available feature collections."""
    base = str(request.base_url).rstrip("/")
    collections = []
    for cid, cdef in COLLECTIONS.items():
        collections.append({
            "id": cdef["id"],
            "title": cdef["title"],
            "description": cdef["description"],
            "extent": cdef["extent"],
            "itemType": cdef["itemType"],
            "crs": cdef["crs"],
            "links": [
                {"href": f"{base}/ogc/collections/{cid}", "rel": "self", "type": "application/json"},
                {"href": f"{base}/ogc/collections/{cid}/items", "rel": "items", "type": "application/geo+json"},
            ],
        })
    return {"collections": collections, "links": [{"href": f"{base}/ogc/collections", "rel": "self"}]}


@router.get("/collections/{collection_id}")
def get_collection(collection_id: str, request: Request):
    """Get collection metadata."""
    if collection_id not in COLLECTIONS:
        raise HTTPException(404, f"Collection '{collection_id}' not found")
    base = str(request.base_url).rstrip("/")
    cdef = COLLECTIONS[collection_id]
    return {
        "id": cdef["id"],
        "title": cdef["title"],
        "description": cdef["description"],
        "extent": cdef["extent"],
        "itemType": cdef["itemType"],
        "crs": cdef["crs"],
        "links": [
            {"href": f"{base}/ogc/collections/{collection_id}", "rel": "self", "type": "application/json"},
            {"href": f"{base}/ogc/collections/{collection_id}/items", "rel": "items", "type": "application/geo+json"},
        ],
    }


def _build_bbox_filter(bbox: list[float]) -> str:
    """Build a spatial filter clause from a bbox [west, south, east, north]."""
    if len(bbox) < 4:
        return ""
    w, s, e, n = bbox[:4]
    return f"ST_Intersects(ST_Transform(geometry, 4326), ST_MakeEnvelope({w}, {s}, {e}, {n}, 4326))"


def _row_to_feature(row: dict, collection_id: str) -> dict:
    """Convert a database row to a GeoJSON Feature."""
    geom = json.loads(row["geom"]) if row.get("geom") else None
    props = {}
    skip_keys = {"geom", "id", "geometry"}

    for k, v in row.items():
        if k in skip_keys:
            continue
        if isinstance(v, dict):
            props.update(v)
        elif hasattr(v, "isoformat"):
            props[k] = v.isoformat()
        else:
            props[k] = v

    return {
        "type": "Feature",
        "id": row.get("id", ""),
        "geometry": geom,
        "properties": props,
    }


@router.get("/collections/{collection_id}/items")
def get_items(
    collection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    bbox: str | None = Query(default=None, description="Bounding box: west,south,east,north"),
    dataset_id: str | None = Query(default=None, description="Filter by dataset ID"),
    status: str | None = Query(default=None, description="Filter by status (parcels only)"),
    conflict_type: str | None = Query(default=None, description="Filter by conflict type"),
):
    """Get features from a collection with optional spatial and attribute filters."""
    if collection_id not in COLLECTIONS:
        raise HTTPException(404, f"Collection '{collection_id}' not found")

    cdef = COLLECTIONS[collection_id]
    base_query = cdef["query"]

    # Build WHERE clauses
    conditions = []
    params: dict[str, Any] = {}

    if bbox:
        try:
            parts = [float(x.strip()) for x in bbox.split(",")]
            if len(parts) >= 4:
                conditions.append(
                    "ST_Intersects(ST_Transform(geometry, 4326), "
                    "ST_MakeEnvelope(:bbox_w, :bbox_s, :bbox_e, :bbox_n, 4326))"
                )
                params["bbox_w"] = parts[0]
                params["bbox_s"] = parts[1]
                params["bbox_e"] = parts[2]
                params["bbox_n"] = parts[3]
        except (ValueError, IndexError):
            pass

    if dataset_id and collection_id == "parcels":
        conditions.append("pv.source_ids @> CAST(:ds AS jsonb)")
        params["ds"] = f'["{dataset_id}"]'
    elif dataset_id and collection_id in ("observations", "survey_points"):
        conditions.append("source_dataset = :ds")
        params["ds"] = dataset_id

    if status and collection_id == "parcels":
        conditions.append("pv.status = :status")
        params["status"] = status

    if conflict_type and collection_id == "conflicts":
        conditions.append("conflict_type = :ctype")
        params["ctype"] = conflict_type

    # Wrap query with filters
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    q = f"SELECT * FROM ({base_query}) sub WHERE {where_clause} LIMIT :lim OFFSET :off"
    params["lim"] = limit
    params["off"] = offset

    count_q = f"SELECT COUNT(*) FROM ({base_query}) sub WHERE {where_clause}"

    rows = db.execute(text(q), params).mappings().all()
    total = db.execute(text(count_q), {k: v for k, v in params.items() if k not in ("lim", "off")}).scalar()

    base_url = str(request.base_url).rstrip("/")
    features = [_row_to_feature(dict(r), collection_id) for r in rows]

    links = [
        {"href": f"{base_url}/ogc/collections/{collection_id}/items?limit={limit}&offset={offset}",
         "rel": "self", "type": "application/geo+json"},
    ]
    if offset + limit < (total or 0):
        links.append({
            "href": f"{base_url}/ogc/collections/{collection_id}/items?limit={limit}&offset={offset + limit}",
            "rel": "next", "type": "application/geo+json",
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "numberMatched": total,
        "numberReturned": len(features),
        "links": links,
    }


@router.get("/collections/{collection_id}/items/{feature_id}")
def get_item(
    collection_id: str,
    feature_id: str,
    db: Session = Depends(get_db),
):
    """Get a single feature by ID."""
    if collection_id not in COLLECTIONS:
        raise HTTPException(404, f"Collection '{collection_id}' not found")

    cdef = COLLECTIONS[collection_id]
    base_query = cdef["query"]
    q = f"SELECT * FROM ({base_query}) sub WHERE id = :fid LIMIT 1"
    row = db.execute(text(q), {"fid": feature_id}).mappings().first()

    if not row:
        raise HTTPException(404, f"Feature '{feature_id}' not found in collection '{collection_id}'")

    return _row_to_feature(dict(row), collection_id)
