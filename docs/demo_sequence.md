# Demo Sequence — AI-Assisted Cadastral Evidence Fusion Platform

## Prerequisites
1. Docker Desktop running (`docker compose up --build -d`)
2. Backend at `http://localhost:8000`
3. Frontend at `http://localhost:5173`
4. Data seeded (auto-runs on first start, or click **Reset Demo**)

---

## Stage 1: Overview (1 min)

**Open the WebGIS at localhost:5173**

> "This is our AI-Assisted Cadastral Evidence Fusion platform. On the left is a
> guided pipeline stepper — each stage of the reconciliation workflow is
> individually executable and auditable. The center shows a MapLibre map with
> multiple registered data layers."

Point out:
- **Layer toggles** (top-right): Truth (green), Legacy (red dashed), Survey (blue),
  Observations (yellow), GNSS (cyan)
- The **offset** between legacy and truth layers shows the registration problem

---

## Stage 2: Create a Pipeline Run (30 sec)

1. In the stepper sidebar, click **New Pipeline Run**
2. Select source: `CAD-LEGACY`, reference: `SURVEY-2026`
3. Click **Create**

> "We're setting up a reconciliation run: aligning the 1998 legacy map against
> the 2026 survey reference."

---

## Stage 3: Registration (1.5 min)

1. Click **Run** on the **Register** stage
2. Watch the status change from pending → running → complete
3. Note the results: method (Helmert → Affine escalation), RMSE, P95

> "Registration uses RANSAC to find reliable control points, then tries
> Helmert similarity first. If any region's P95 exceeds 1.5× the uncertainty
> envelope, it escalates to an affine transform. Watch the SE quadrant —
> the legacy map has a stronger warp there."

4. Toggle **Show Heatmap** to see residual visualization
5. Click **Before/After** toggle to compare original vs registered positions

> "The heatmap shows registration quality per grid cell. Red cells indicate
> areas where the legacy sheet's distortion exceeds the affine model — these
> regions get extra scrutiny during matching."

---

## Stage 4: Extract Evidence (30 sec)

1. Click **Run** on the **Extract** stage
2. Toggle the **Observations** layer

> "We extract physical evidence from the synthetic ORI imagery — building
> footprints and boundary walls. Each observation carries a confidence score
> and positional uncertainty. These become the 'physical_support' dimension
> in our six-dimensional evidence fusion."

---

## Stage 5: Conflation & Matching (1.5 min)

1. Click **Run** on **Conflate**
2. Check the match statistics in the stage result panel

> "The conflation engine uses PostGIS ST_DWithin with a σ-based search
> radius — each source parcel only considers candidates within its
> uncertainty envelope. The matcher model (LogisticRegression trained on
> labelled pairs) produces calibrated probabilities."

3. Toggle the **Matches** layer — grey lines connect matched centroids
4. Point out split (P-104 → P-202, P-203) and merge (P-020+P-021 → P-230)

---

## Stage 6: Evidence Fusion (30 sec)

1. Click **Run** on **Fuse Evidence**
2. Click on any parcel to see the **Evidence Panel**

> "We maintain SIX separate evidence dimensions — never collapsing to one
> number. Geometry compatibility, attribute agreement, GNSS survey support,
> physical evidence from imagery, temporal recency, and identity matching.
> The radar chart shows each dimension independently."

---

## Stage 7: Conflict Detection (1 min)

1. Click **Run** on **Detect Conflicts**
2. Toggle the **Conflicts** layer — red highlights on problematic parcels
3. Click on P-103 (Case B: attribute conflict)

> "Case B: the geometry matches, but revenue area differs by 19% and the
> survey number is ambiguous. The auto-accept checklist fails — this goes
> to human review. Case A: P-015 has a 4m displacement that exceeds the
> uncertainty envelope."

---

## Stage 8: Routing & Review (1.5 min)

1. Click **Run** on **Route**
2. Switch to the **Review** tab in the detail panel
3. Browse the review queue

> "9 conditions must ALL pass for auto-accept: valid geometry, regional
> registration pass, probability above 0.90, no conflicts, within
> uncertainty envelope, complete provenance, and unchanged legal state.
> For this demo, ~70% of parcels auto-accept; the rest go to review."

4. Click on a review case → see the full evidence snapshot
5. Use APPROVE/REJECT buttons with a reason

> "Every review action is logged with actor, role, timestamp, and notes.
> 10% of auto-accepted parcels are randomly sampled for audit."

---

## Stage 9: Governance Demo (1 min)

Switch to the **Governance** tab:

1. Show the state machine diagram (OBSERVED → CANDIDATE → ... → PUBLISHED)
2. Click **Try AI → Publish**

> "This is TC15: our invariant enforcement. The AI_SERVICE role is
> blocked from setting status to APPROVED or PUBLISHED — both at the
> application layer (create_candidate_version checks) AND at the
> database layer (a PostgreSQL trigger). Watch:"

3. See the red alert box: "Application rejected: AI_SERVICE cannot set PUBLISHED"
4. Point out the audit log showing the blocked attempt

---

## Stage 10: Metrics & OGC API (1 min)

1. Switch to the **Metrics** tab
   - Registration: method, RMSE, regional breakdown
   - Matching: precision, recall
   - Click **Evaluate Benchmark** to see ground-truth comparison

2. Show the OGC API:
   - Open `http://localhost:8000/ogc/collections` in a new tab
   - Browse `http://localhost:8000/ogc/collections/parcels/items?limit=5`

> "Full OGC API Features Part 1 compliance: collections, items,
> bounding-box queries, pagination. Any GIS client can connect."

---

## Key Architectural Points to Emphasize

| Principle | Implementation |
|---|---|
| AI produces observations only | Status state machine + DB trigger |
| Whole-layer registration | Never per-parcel transforms |
| Six evidence dimensions | Never collapsed to one score |
| Uncertainty propagation | σ-based search radius, regional P95 |
| Split/merge detection | Union coverage, not summed IoU |
| Versioned parcels | Immutable versions, provenance chain |
| 9-condition auto-accept | All must pass, checklist persisted |
| 10% random audit | Automatic sampling of auto-accepted |

---

## Resetting the Demo

Click **Reset Demo** in the top-left corner, or:

```bash
curl -X POST http://localhost:8000/demo/reset
```

This truncates all tables and re-seeds the synthetic data.
