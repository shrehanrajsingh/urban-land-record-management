# Competition demo sequence (≈10 steps)

1. Open WebGIS at http://localhost:5173 — load shows legacy (orange) vs truth (green) misalignment, GNSS points.
2. Observe misalignment before registration.
3. Click **Run pipeline** — registration aligns the legacy sheet (whole-layer Helmert/affine).
4. Inspect demo state: registration RMSE / uncertainty.
5. Conflation results appear: 1:1 matches, split (P-104), review queue items.
6. Open the Case B review item for **P-103** — geometry compatible, attribute/identity conflict.
7. Evidence panel shows match probability, registration P95, geometry/survey/attribute badges.
8. Reviewer clicks **Approve** (or Edit / Field Verify / Reject).
9. Click **Why this parcel?** — versioned provenance lineage.
10. Click **AI → Publish** — database/application rejects `AI_SERVICE → PUBLISHED`.

Frozen claim language: the platform registers, conflates with uncertainty-aware split/merge, fuses evidence, routes conflicts, and enforces governance. It does **not** determine legal ownership or boundaries autonomously.
