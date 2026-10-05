#!/usr/bin/env python3
"""Seed synthetic benchmark layers, GNSS, control points, and imagery.

Thin wrapper around ``benchmark.scenario.generate_scenario`` and the
imagery renderers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from geoalchemy2.elements import WKTElement
from sqlalchemy import text

from backend.db import SessionLocal, engine
from backend.models.schema import Dataset, DatasetAsset, SurveyPoint
from backend.services.ingestion.ingest import ingest_geodataframe
from backend.services.ids import new_id
from benchmark.imagery.render import render_synthetic_dsm_dtm, render_synthetic_ori
from benchmark.scenario import generate_scenario
from ml.matcher.model import MatchProbabilityModel, generate_synthetic_training_pairs


def seed(db_session=None) -> None:
    """Run the full benchmark seed.

    If *db_session* is ``None`` a new ``SessionLocal`` is created and closed
    automatically.  Pass an existing session (e.g. from FastAPI ``Depends``)
    to reuse it.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    try:
        command.upgrade(cfg, "head")
    except Exception as e:  # noqa: BLE001
        print(f"Alembic upgrade note: {e}")

    own_session = db_session is None
    db = db_session or SessionLocal()

    try:
        existing = db.execute(text("SELECT COUNT(*) FROM datasets")).scalar()
        if existing and existing > 0:
            print("Benchmark already seeded; skipping.")
            return

        # ------------------------------------------------------------------
        # 1. Generate scenario
        # ------------------------------------------------------------------
        scenario = generate_scenario(seed=42)
        truth = scenario["truth"]
        survey = scenario["survey"]
        legacy = scenario["legacy"]
        gnss = scenario["gnss"]
        ground_truth = scenario["ground_truth"]
        control_points = scenario["control_points"]

        # ------------------------------------------------------------------
        # 2. Render synthetic imagery
        # ------------------------------------------------------------------
        imagery_dir = ROOT / "data" / "benchmark" / "imagery"
        imagery_dir.mkdir(parents=True, exist_ok=True)

        ori_path = str(imagery_dir / "ori.tif")
        ori_meta = render_synthetic_ori(survey, ori_path, gsd=0.25, seed=42)

        dsm_path = str(imagery_dir / "dsm.tif")
        dtm_path = str(imagery_dir / "dtm.tif")
        elev_meta = render_synthetic_dsm_dtm(survey, dsm_path, dtm_path, gsd=0.5, seed=42)

        # ------------------------------------------------------------------
        # 3. Ingest layers
        # ------------------------------------------------------------------
        ingest_geodataframe(
            db, truth,
            dataset_id="CAD-TRUTH",
            name="Synthetic Truth Cadastre",
            source_type="reference",
            status="OBSERVED",
            created_by="seed",
            created_by_role="SYSTEM",
        )

        ingest_geodataframe(
            db, survey,
            dataset_id="SURVEY-2026",
            name="New Survey Reference Layer",
            source_type="survey",
            status="OBSERVED",
            created_by="seed",
            created_by_role="SYSTEM",
        )

        ingest_geodataframe(
            db, legacy,
            dataset_id="CAD-LEGACY",
            name="Distorted Legacy Cadastre",
            source_type="cadastral_map",
            status="OBSERVED",
            created_by="seed",
            created_by_role="SYSTEM",
        )

        # ------------------------------------------------------------------
        # 4. Store GNSS survey points
        # ------------------------------------------------------------------
        for _, row in gnss.iterrows():
            db.add(
                SurveyPoint(
                    id=row["id"],
                    geometry=WKTElement(row.geometry.wkt, srid=32643),
                    point_type="GNSS",
                    uncertainty_m=float(row["uncertainty_m"]),
                    source_dataset="GNSS-2026",
                    properties={"parcel_id": row["parcel_id"]},
                )
            )

        # ------------------------------------------------------------------
        # 5. Store metadata (ground truth + control points)
        # ------------------------------------------------------------------
        db.add(
            Dataset(
                id="BENCH-META",
                name="Benchmark Metadata",
                source_type="meta",
                crs="EPSG:32643",
                metadata_json={
                    "ground_truth": ground_truth,
                    "control_points": control_points,
                    "imagery": {
                        "ori": ori_meta,
                        "dsm": {"path": dsm_path, **elev_meta},
                    },
                },
            )
        )

        # ------------------------------------------------------------------
        # 6. Register imagery as dataset assets
        # ------------------------------------------------------------------
        db.add(DatasetAsset(
            id=new_id("DA"), dataset_id="SURVEY-2026",
            asset_type="ORI", path=ori_path,
            metadata_json=ori_meta,
        ))
        db.add(DatasetAsset(
            id=new_id("DA"), dataset_id="SURVEY-2026",
            asset_type="DSM", path=dsm_path,
            metadata_json=elev_meta,
        ))
        if dtm_path:
            db.add(DatasetAsset(
                id=new_id("DA"), dataset_id="SURVEY-2026",
                asset_type="DTM", path=dtm_path,
                metadata_json={"dtm_path": dtm_path},
            ))

        db.commit()

        # ------------------------------------------------------------------
        # 7. Train matcher model
        # ------------------------------------------------------------------
        model = MatchProbabilityModel()
        X, y = generate_synthetic_training_pairs(1000)
        rng = np.random.default_rng(42)
        idx = np.arange(len(y))
        rng.shuffle(idx)
        train = idx[: int(0.6 * len(y))]
        model.fit(X[train], y[train])
        out = ROOT / "data" / "models" / "matcher.pkl"
        out.parent.mkdir(parents=True, exist_ok=True)
        model.save(out)

        # ------------------------------------------------------------------
        # 8. Export GeoJSON for offline inspection
        # ------------------------------------------------------------------
        export_dir = ROOT / "data" / "benchmark"
        export_dir.mkdir(parents=True, exist_ok=True)
        truth.to_file(export_dir / "truth.geojson", driver="GeoJSON")
        survey.to_file(export_dir / "survey.geojson", driver="GeoJSON")
        legacy.to_file(export_dir / "legacy.geojson", driver="GeoJSON")
        (export_dir / "ground_truth.json").write_text(
            json.dumps(ground_truth, indent=2, default=str)
        )
        (export_dir / "control_points.json").write_text(
            json.dumps(control_points, indent=2)
        )

        print(
            f"Seeded truth={len(truth)} survey={len(survey)} "
            f"legacy={len(legacy)} gnss={len(gnss)} "
            f"control_points={len(control_points)}"
        )

    finally:
        if own_session:
            db.close()


def main() -> None:
    seed()


if __name__ == "__main__":
    main()
