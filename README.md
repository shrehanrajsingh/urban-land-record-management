# AI-Assisted Cadastral Evidence Fusion & Reconciliation Platform

A geospatial platform that reconciles heterogeneous urban land datasets — legacy
cadastral maps, drone imagery, GNSS surveys, and municipal GIS — into versioned,
evidence-backed cadastral candidates. **AI produces observations and candidates
only, never authoritative records** (enforced at both application and database
levels).

## Quick Start

```bash
# Start all services
docker compose up --build -d

# Frontend: http://localhost:5173
# API:      http://localhost:8000
# OGC API:  http://localhost:8000/ogc/collections
```

The benchmark data is seeded automatically on first start.

## Architecture

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   React WebGIS   │───▶│   FastAPI + OGC   │───▶│ PostGIS 16/3.4   │
│  MapLibre GL JS  │    │   Pipeline API    │    │ EPSG:32643       │
│  Pipeline Stepper│    │   Evidence Fusion  │    │ Status trigger   │
└──────────────────┘    └──────────────────┘    └──────────────────┘
         │                      │
         │              ┌───────┴───────┐
         │              │  Geospatial   │
         │              │  Registration │ Helmert → Affine → TPS
         │              │  RANSAC       │ with regional P95
         │              │  Conflation   │ σ-based search radius
         │              │  Split/Merge  │ Union coverage
         │              └───────────────┘
         │              ┌───────────────┐
         │              │    ML Layer   │
         │              │  LogReg/GBM   │ 60/20/20 split
         │              │  Platt calib  │ Isotonic regression
         │              │  Building ext │ Image processing
         │              └───────────────┘
```

## 7-Stage Pipeline

| Stage | Purpose | Key Algorithm |
|-------|---------|---------------|
| **Ingest** | Load parcel layers into PostGIS | GeoDataFrame → ParcelVersion |
| **Register** | Align legacy sheet to reference | RANSAC + Helmert/Affine/TPS escalation |
| **Extract** | Find physical evidence from ORI | Color segmentation + edge detection |
| **Conflate** | Match source ↔ target parcels | PostGIS ST_DWithin + LR matcher |
| **Fuse** | Combine 6 evidence dimensions | Weighted precedence fusion |
| **Detect Conflicts** | Flag problems | Attribute, displacement, overlap, ambiguity |
| **Route** | Auto-accept or send to review | 9-condition AND checklist |

## Six Evidence Dimensions

Never collapsed to a single score:

1. **Geometry Compatibility** — IoU, area ratio, shape similarity
2. **Attribute Compatibility** — Revenue area, land use, owner
3. **Survey Support** — GNSS point proximity
4. **Physical Support** — Building/wall intersection
5. **Temporal Support** — Dataset recency
6. **Identity Compatibility** — Survey number, parcel ID matching

## Governance

- **State machine**: OBSERVED → CANDIDATE → VALIDATED → APPROVED → PUBLISHED
- **AI_SERVICE blocked** from APPROVED/PUBLISHED:
  - Application check in `create_candidate_version()`
  - PostgreSQL trigger `enforce_parcel_status_transition()`
- **10% random audit** of auto-accepted results
- **Provenance chain** on every parcel version

## Project Structure

```
backend/
  api/
    main.py              FastAPI app + all endpoints
    ogc/features.py      OGC API Features Part 1
    schemas.py           Pydantic models
  services/
    pipeline/            7-stage pipeline orchestration
    registration/        Escalation-based registration
    conflation/          σ-aware matching engine
    conflicts/           Conflict detection + checklist
    evidence/            6-dim fusion + physical extraction
    review/              Review workflow + auto-accept
    ingestion/           GeoDataFrame → DB
  models/
    schema.py            17 SQLAlchemy models
    enums.py             Status enums
  db.py                  Engine + session
  config.py              Settings

geospatial/
  registration/
    transforms.py        Helmert, affine, TPS math
    homologous.py        Corner point extraction
    ransac.py            RANSAC robust estimation
    escalation.py        Method escalation hierarchy
  geometry/
    features.py          11 pair features
    uncertainty.py       σ propagation
  split_merge/
    detect.py            Split/merge/boundary adjustment
  topology/
    validate.py          Geometry validation

ml/
  matcher/
    model.py             MatchProbabilityModel
    features.py          Feature columns + vectorizer
    train.py             Training pipeline
  calibration/
    calibrate.py         Platt + isotonic
  building/
    extract.py           ORI image → buildings/walls

benchmark/
  scenario.py            Deterministic scenario generator
  imagery/render.py      Synthetic ORI/DSM/DTM renderer
  truth/generate.py      Grid fabric generator
  distortions/distort.py Legacy distortion
  evaluation/metrics.py  Ground-truth metrics

web/src/
  App.jsx                Main orchestrator
  api.js                 API client
  components/
    PipelineStepper.jsx  7-stage guided stepper
    MapView.jsx          MapLibre map + layers
    LayerToggles.jsx     Layer visibility controls
    EvidencePanel.jsx    6-dim radar chart
    ReviewWorkspace.jsx  Review queue + actions
    HistoryTimeline.jsx  Version timeline
    MetricsPanel.jsx     Registration/matching metrics
    GovernancePanel.jsx  TC15 demo + audit log
    HeatmapOverlay.jsx   Registration residual heatmap
    ParcelDetail.jsx     Parcel info panel

tests/                   50 tests (pure Python, no DB required)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/pipeline_runs` | Create a pipeline run |
| POST | `/pipeline_runs/{id}/stages/{stage}` | Run a single stage |
| POST | `/pipeline_runs/{id}/run_all` | Run all 7 stages |
| GET | `/pipeline_runs/{id}` | Get run status + stage details |
| POST | `/pipelines/integrate` | Legacy one-shot integration |
| GET | `/parcels/geojson` | GeoJSON parcels (WGS84) |
| GET | `/observations/geojson` | Physical evidence observations |
| GET | `/survey_points/geojson` | GNSS survey points |
| GET | `/matches` | Candidate matches |
| GET | `/conflicts` | Detected conflicts |
| GET | `/review_cases` | Review queue |
| POST | `/review_cases/{id}/actions` | Submit review action |
| GET | `/parcels/{id}/versions` | Parcel version history |
| GET | `/parcels/{id}/provenance` | Provenance chain |
| POST | `/demo/ai_publish` | TC15: AI → Publish (must fail) |
| POST | `/demo/ai_publish_raw` | TC15: Raw SQL attempt |
| POST | `/demo/reset` | Reset demo data |
| GET | `/benchmark/evaluate` | Ground-truth metrics |
| GET | `/ogc/collections` | OGC API collections |
| GET | `/ogc/collections/{id}/items` | OGC API features |

## Running Tests

```bash
# All tests (no DB required)
PYTHONPATH=. pytest tests/ -v

# With DB (requires PostGIS on port 5433)
RUN_DB_TESTS=1 PYTHONPATH=. pytest tests/ -v
```

## Demo Reset

```bash
curl -X POST http://localhost:8000/demo/reset
```

Or click **Reset Demo** in the WebGIS sidebar.

## License

Proprietary — all rights reserved.
