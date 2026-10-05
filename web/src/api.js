const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function req(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export const api = {
  health: () => req("/health"),
  demoState: () => req("/demo/state"),
  parcels: (datasetId) =>
    req(`/parcels/geojson${datasetId ? `?dataset_id=${datasetId}` : ""}`),
  observations: () => req("/observations/geojson"),
  surveyPoints: () => req("/survey_points/geojson"),
  controlPoints: () => req("/control_points"),
  integrate: (body = {}) =>
    req("/pipelines/integrate", { method: "POST", body: JSON.stringify(body) }),
  reviewCases: () => req("/review_cases"),
  reviewCase: (id) => req(`/review_cases/${id}`),
  reviewAction: (id, body) =>
    req(`/review_cases/${id}/actions`, { method: "POST", body: JSON.stringify(body) }),
  registrationRuns: () => req("/registration_runs"),
  provenance: (parcelId) => req(`/parcels/${parcelId}/provenance`),
  versions: (parcelId) => req(`/parcels/${parcelId}/versions`),
  aiPublish: (parcelId) =>
    req("/demo/ai_publish", {
      method: "POST",
      body: JSON.stringify({ parcel_id: parcelId, actor_role: "AI_SERVICE" }),
    }),
  matches: () => req("/matches"),
  matchesGeoJSON: () => req("/matches/geojson"),
  conflicts: () => req("/conflicts"),
  conflictsGeoJSON: () => req("/conflicts/geojson"),
  registeredParcels: () => req("/parcels/registered/geojson"),
  auditEvents: () => req("/audit_events").catch(() => []),
  modelRuns: () => req("/model_runs").catch(() => []),

  // Pipeline run endpoints
  createPipelineRun: (sourceId, referenceId) =>
    req("/pipeline_runs", {
      method: "POST",
      body: JSON.stringify({ source_dataset_id: sourceId, reference_dataset_id: referenceId }),
    }),
  runStage: (runId, stageName, params = {}) =>
    req(`/pipeline_runs/${runId}/stages/${stageName}`, {
      method: "POST",
      body: JSON.stringify(params),
    }),
  runAllStages: (runId, params = {}) =>
    req(`/pipeline_runs/${runId}/run_all`, {
      method: "POST",
      body: JSON.stringify(params),
    }),
  getPipelineRun: (runId) => req(`/pipeline_runs/${runId}`),
  listPipelineRuns: () => req("/pipeline_runs").catch(() => []),
  resetDemo: () => req("/demo/reset", { method: "POST" }),
  getRegistrationHeatmap: (runId) =>
    req(`/pipeline_runs/${runId}/registration_heatmap`).catch(() => null),
  getMatchDetails: (matchId) => req(`/matches/${matchId}`),
  getParcelVersions: (parcelId) => req(`/parcels/${parcelId}/versions`),
  getParcelProvenance: (parcelId) => req(`/parcels/${parcelId}/provenance`),
  submitReviewAction: (caseId, action, reason) =>
    req(`/review_cases/${caseId}/actions`, {
      method: "POST",
      body: JSON.stringify({
        action,
        actor: "reviewer-07",
        actor_role: "REVIEWER",
        notes: reason || `Review ${action}`,
        payload: {},
      }),
    }),
};
