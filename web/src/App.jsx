import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import PipelineStepper from "./components/PipelineStepper.jsx";
import MapView, { boundsFromGeoJSON } from "./components/MapView.jsx";
import LayerToggles from "./components/LayerToggles.jsx";
import ParcelDetail from "./components/ParcelDetail.jsx";
import EvidencePanel from "./components/EvidencePanel.jsx";
import ReviewWorkspace from "./components/ReviewWorkspace.jsx";
import HistoryTimeline from "./components/HistoryTimeline.jsx";
import MetricsPanel from "./components/MetricsPanel.jsx";
import GovernancePanel from "./components/GovernancePanel.jsx";
import HeatmapOverlay from "./components/HeatmapOverlay.jsx";

const DEFAULT_VISIBLE = {
  truth: true,
  legacy: true,
  survey: true,
  registered: true,
  observations: true,
  gnss: true,
  matches: true,
  conflicts: true,
};

const DETAIL_TABS = ["Evidence", "Review", "History", "Metrics", "Governance"];

export default function App() {
  // Pipeline state
  const [pipelineRuns, setPipelineRuns] = useState([]);
  const [currentRunId, setCurrentRunId] = useState(null);
  const [stageStatuses, setStageStatuses] = useState({});
  const [stageResults, setStageResults] = useState({});

  // Map layers
  const [layers, setLayers] = useState({});
  const [visibleLayers, setVisibleLayers] = useState(DEFAULT_VISIBLE);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [heatmapData, setHeatmapData] = useState(null);

  // Selection & detail
  const [selectedParcelId, setSelectedParcelId] = useState(null);
  const [selectedProps, setSelectedProps] = useState(null);
  const [detailTab, setDetailTab] = useState("Evidence");

  // Review
  const [cases, setCases] = useState([]);
  const [selectedCaseId, setSelectedCaseId] = useState(null);
  const [caseDetail, setCaseDetail] = useState(null);

  // Metrics / registration
  const [registration, setRegistration] = useState(null);
  const [modelRuns, setModelRuns] = useState([]);

  // Status
  const [status, setStatus] = useState("Ready");
  const [loading, setLoading] = useState(false);

  // ── Parse stage results into display shape ──
  const parseStageResult = useCallback((stageName, data) => {
    if (!data) return {};
    switch (stageName) {
      case "register":
        return {
          method: data.method,
          rmse: data.global_rmse,
          p95: data.uncertainty_m,
          residual_count: data.residuals?.length || data.transformed_count,
          passed: data.passed,
        };
      case "extract": {
        const obs = data.observations || [];
        const buildings = obs.filter((o) => o.type === "BUILDING").length;
        const walls = obs.filter((o) => o.type === "WALL").length;
        return { buildings, walls, total: obs.length };
      }
      case "conflate": {
        const matches = data.matches || [];
        const byRel = {};
        matches.forEach((m) => {
          const r = m.relation || "UNKNOWN";
          byRel[r] = (byRel[r] || 0) + 1;
        });
        return { match_count: matches.length, by_relation: byRel };
      }
      case "fuse": {
        const fused = data.fused_matches || [];
        // Average evidence dims across all fused matches
        const dims = {};
        const dimKeys = [
          "geometry_compatibility", "attribute_compatibility",
          "survey_support", "physical_support",
          "temporal_support", "identity_compatibility",
        ];
        dimKeys.forEach((k) => {
          const vals = fused.map((m) => m.fused?.[k] ?? m.evidence?.[k]).filter((v) => v != null);
          dims[k] = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
        });
        return { ...dims, total: fused.length };
      }
      case "detect_conflicts": {
        const conflicts = data.conflicts || [];
        const byType = {};
        conflicts.forEach((c) => {
          const t = c.type || "UNKNOWN";
          byType[t] = (byType[t] || 0) + 1;
        });
        return { conflict_count: conflicts.length, count: conflicts.length, by_type: byType };
      }
      case "route": {
        const candidates = data.candidates || [];
        const auto = candidates.filter((c) => c.status === "VALIDATED").length;
        const review = candidates.filter((c) => c.status === "REVIEW").length;
        return { auto_accepted: auto, review_count: review };
      }
      default:
        return data;
    }
  }, []);

  // ── Load data ──
  const refreshLayers = useCallback(async () => {
    try {
      const [truth, legacy, obs, survey, registered, matchesGeo, conflictsGeo, casesData] = await Promise.all([
        api.parcels("CAD-TRUTH").catch(() => null),
        api.parcels("CAD-LEGACY").catch(() => null),
        api.observations().catch(() => null),
        api.surveyPoints().catch(() => null),
        api.registeredParcels().catch(() => null),
        api.matchesGeoJSON().catch(() => ({ type: "FeatureCollection", features: [] })),
        api.conflictsGeoJSON().catch(() => ({ type: "FeatureCollection", features: [] })),
        api.reviewCases().catch(() => []),
      ]);

      setLayers((prev) => ({
        ...prev,
        truth,
        legacy,
        observations: obs,
        gnss: survey,
        registered: registered?.type === "FeatureCollection" && registered.features.length > 0 ? registered : null,
        matches: matchesGeo?.type === "FeatureCollection" && matchesGeo.features.length > 0 ? matchesGeo : null,
        conflicts: conflictsGeo?.type === "FeatureCollection" && conflictsGeo.features.length > 0 ? conflictsGeo : null,
      }));

      setCases(Array.isArray(casesData) ? casesData : []);
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
  }, []);

  const refreshRegistration = useCallback(async () => {
    try {
      const runs = await api.registrationRuns().catch(() => []);
      const list = Array.isArray(runs) ? runs : [];
      if (list.length > 0) {
        setRegistration(list[list.length - 1]);
      }
    } catch {}
  }, []);

  const refreshModelRuns = useCallback(async () => {
    try {
      const runs = await api.modelRuns();
      setModelRuns(Array.isArray(runs) ? runs : []);
    } catch {}
  }, []);

  const refreshPipelineRuns = useCallback(async () => {
    try {
      const runs = await api.listPipelineRuns();
      setPipelineRuns(Array.isArray(runs) ? runs : []);
      return Array.isArray(runs) ? runs : [];
    } catch {
      return [];
    }
  }, []);

  // Initial load
  useEffect(() => {
    refreshLayers();
    refreshRegistration();
    refreshModelRuns();
    refreshPipelineRuns();
  }, [refreshLayers, refreshRegistration, refreshModelRuns, refreshPipelineRuns]);

  // Load case detail
  useEffect(() => {
    if (!selectedCaseId) { setCaseDetail(null); return; }
    api.reviewCase(selectedCaseId)
      .then(setCaseDetail)
      .catch(() => setCaseDetail(null));
  }, [selectedCaseId]);

  // Focus bounds
  const focusBounds = useMemo(
    () => boundsFromGeoJSON(layers.truth || layers.legacy),
    [layers.truth, layers.legacy]
  );

  // ── Pipeline actions ──
  const handleCreateRun = async () => {
    setStatus("Creating pipeline run…");
    try {
      const run = await api.createPipelineRun("CAD-LEGACY", "SURVEY-2026");
      setCurrentRunId(run.id);
      setStageStatuses({});
      setStageResults({});
      await refreshPipelineRuns();
      setStatus(`Pipeline run ${run.id} created`);
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
  };

  const handleRunStage = async (stageName) => {
    if (!currentRunId) return;
    setStageStatuses((prev) => ({ ...prev, [stageName]: "running" }));
    setStatus(`Running ${stageName}…`);
    try {
      const rawResult = await api.runStage(currentRunId, stageName);
      // The API returns {stage, result} — extract the inner result
      const stageData = rawResult.result || rawResult;
      // Normalize into the shape the stepper expects
      const parsed = parseStageResult(stageName, stageData);
      setStageStatuses((prev) => ({ ...prev, [stageName]: "complete" }));
      setStageResults((prev) => ({ ...prev, [stageName]: parsed }));
      setStatus(`${stageName} complete`);
      await refreshLayers();
      await refreshRegistration();
    } catch (e) {
      setStageStatuses((prev) => ({ ...prev, [stageName]: "failed" }));
      setStatus(`${stageName} failed: ${e.message}`);
    }
  };

  const handleRunAll = async () => {
    if (!currentRunId) {
      // Fall back to integrate endpoint
      setStatus("Running full pipeline…");
      setLoading(true);
      try {
        const result = await api.integrate({
          source_dataset_id: "CAD-LEGACY",
          reference_dataset_id: "SURVEY-2026",
          uncertainty_envelope_m: 1.0,
        });
        const stages = ["ingest", "register", "extract", "conflate", "fuse", "detect_conflicts", "route"];
        const newStatuses = {};
        stages.forEach((s) => { newStatuses[s] = "complete"; });
        setStageStatuses(newStatuses);
        setStageResults((prev) => ({
          ...prev,
          register: result.registration || { rmse: result.rmse },
          conflate: { match_count: result.matches?.length || 0 },
          detect_conflicts: { count: result.conflicts?.count || 0 },
          route: {
            auto_accepted: result.auto_accepted ?? 0,
            review_count: result.review_count ?? result.matches?.length ?? 0,
          },
        }));
        setStatus(
          result.status === "OK"
            ? `Pipeline OK — ${result.matches?.length || 0} matches, ${result.conflicts?.count || 0} conflicts`
            : `Done: ${result.status}`
        );
        await refreshLayers();
        await refreshRegistration();
        await refreshModelRuns();
        const newCases = await api.reviewCases().catch(() => []);
        setCases(Array.isArray(newCases) ? newCases : []);
        if (newCases.length > 0 && !selectedCaseId) {
          setSelectedCaseId(newCases[0].id);
        }
      } catch (e) {
        setStatus(`Pipeline error: ${e.message}`);
      } finally {
        setLoading(false);
      }
      return;
    }

    setStatus("Running all stages…");
    setLoading(true);
    try {
      const result = await api.runAllStages(currentRunId);
      const stageNames = ["ingest", "register", "extract", "conflate", "fuse", "detect_conflicts", "route"];
      const newStatuses = {};
      stageNames.forEach((s) => { newStatuses[s] = result.status === "COMPLETED" ? "complete" : "failed"; });
      setStageStatuses(newStatuses);

      // Parse each sub-result
      const parsedResults = {};
      if (result.register) parsedResults.register = parseStageResult("register", result.register);
      if (result.extract) parsedResults.extract = parseStageResult("extract", result.extract);
      if (result.conflate) parsedResults.conflate = parseStageResult("conflate", result.conflate);
      if (result.fuse) parsedResults.fuse = parseStageResult("fuse", result.fuse);
      if (result.detect_conflicts) parsedResults.detect_conflicts = parseStageResult("detect_conflicts", result.detect_conflicts);
      if (result.route) parsedResults.route = parseStageResult("route", result.route);
      setStageResults(parsedResults);

      setStatus(`Pipeline ${result.status || "complete"}`);
      await refreshLayers();
      await refreshRegistration();
      await refreshModelRuns();
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleResetDemo = async () => {
    setStatus("Resetting demo…");
    try {
      await api.resetDemo();
      setStageStatuses({});
      setStageResults({});
      setCases([]);
      setSelectedCaseId(null);
      setCaseDetail(null);
      setSelectedParcelId(null);
      setRegistration(null);
      setHeatmapData(null);
      await refreshLayers();
      await refreshPipelineRuns();
      setStatus("Demo reset complete");
    } catch (e) {
      setStatus(`Reset error: ${e.message}`);
    }
  };

  // ── Map interactions ──
  const handleParcelClick = (id, properties) => {
    setSelectedParcelId(id);
    setSelectedProps(properties);
    setDetailTab("Evidence");
  };

  const handleToggleLayer = (key) => {
    setVisibleLayers((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleToggleHeatmap = async () => {
    const next = !showHeatmap;
    setShowHeatmap(next);
    if (next && !heatmapData && currentRunId) {
      try {
        const data = await api.getRegistrationHeatmap(currentRunId);
        // Extract the GeoJSON FeatureCollection from the wrapper
        const geojson = data?.heatmap || data;
        if (geojson?.type === "FeatureCollection") {
          setHeatmapData(geojson);
        }
      } catch {}
    }
  };

  // ── Review actions ──
  const handleSubmitReview = async (caseId, action, reason) => {
    setStatus(`Submitting ${action}…`);
    try {
      await api.submitReviewAction(caseId, action, reason);
      setStatus(`${action} recorded`);
      await refreshLayers();
      if (caseId === selectedCaseId) {
        const updated = await api.reviewCase(caseId).catch(() => null);
        setCaseDetail(updated);
      }
      const newCases = await api.reviewCases().catch(() => []);
      setCases(Array.isArray(newCases) ? newCases : []);
    } catch (e) {
      setStatus(`Action error: ${e.message}`);
    }
  };

  // Evidence from selected case
  const evidence = caseDetail?.match?.evidence || caseDetail?.evidence_snapshot || {};
  const hasDetail = selectedParcelId || selectedCaseId;

  return (
    <div className={`app-layout ${hasDetail ? "detail-open" : ""}`}>
      {/* ── Left: Pipeline Stepper ── */}
      <PipelineStepper
        pipelineRuns={pipelineRuns}
        currentRunId={currentRunId}
        onSelectRun={setCurrentRunId}
        onCreateRun={handleCreateRun}
        onRunStage={handleRunStage}
        onRunAll={handleRunAll}
        onResetDemo={handleResetDemo}
        stageStatuses={stageStatuses}
        stageResults={stageResults}
        status={status}
      />

      {/* ── Center: Map ── */}
      <div className="map-area">
        <MapView
          layers={layers}
          visibleLayers={visibleLayers}
          focusBounds={focusBounds}
          selectedParcelId={selectedParcelId}
          onParcelClick={handleParcelClick}
          heatmapData={heatmapData}
          showHeatmap={showHeatmap}
        />
        <LayerToggles
          visible={visibleLayers}
          onToggle={handleToggleLayer}
          showHeatmap={showHeatmap}
          onToggleHeatmap={handleToggleHeatmap}
        />
        <HeatmapOverlay visible={showHeatmap} />
      </div>

      {/* ── Right: Detail Panel ── */}
      {hasDetail && (
        <div className="detail-panel">
          <div className="detail-panel-header">
            <h2>{selectedParcelId ? `Parcel ${selectedParcelId}` : "Review"}</h2>
            <button
              className="detail-close"
              onClick={() => { setSelectedParcelId(null); setSelectedCaseId(null); setCaseDetail(null); }}
            >
              ✕
            </button>
          </div>

          {/* Tabs */}
          <div className="tab-bar">
            {DETAIL_TABS.map((tab) => (
              <button
                key={tab}
                className={`tab ${detailTab === tab ? "active" : ""}`}
                onClick={() => setDetailTab(tab)}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Tab content */}
          {detailTab === "Evidence" && (
            <div style={{ padding: "0 1rem 1rem" }}>
              {selectedParcelId && (
                <ParcelDetail
                  parcelId={selectedParcelId}
                  properties={selectedProps}
                />
              )}
              <EvidencePanel
                evidence={evidence}
                matchProbability={caseDetail?.match?.match_probability}
                uncertaintyM={caseDetail?.registration?.uncertainty_m}
                relationType={caseDetail?.match?.relation_type}
              />
            </div>
          )}

          {detailTab === "Review" && (
            <ReviewWorkspace
              cases={cases}
              selectedCaseId={selectedCaseId}
              onSelectCase={setSelectedCaseId}
              caseDetail={caseDetail}
              onSubmitAction={handleSubmitReview}
              loading={loading}
            />
          )}

          {detailTab === "History" && (
            <div style={{ padding: "0 1rem 1rem" }}>
              <HistoryTimeline parcelId={selectedParcelId || caseDetail?.match?.target_parcel_ids?.[0]} />
            </div>
          )}

          {detailTab === "Metrics" && (
            <MetricsPanel
              registration={registration}
              modelRuns={modelRuns}
            />
          )}

          {detailTab === "Governance" && (
            <GovernancePanel onStatusUpdate={setStatus} />
          )}
        </div>
      )}
    </div>
  );
}
