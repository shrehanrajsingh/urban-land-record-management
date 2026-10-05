import { useState } from "react";

const STAGES = [
  { key: "ingest", label: "Ingest", num: 1, prereqs: [] },
  { key: "register", label: "Register", num: 2, prereqs: ["ingest"] },
  { key: "extract", label: "Extract", num: 3, prereqs: ["ingest"] },
  { key: "conflate", label: "Conflate", num: 4, prereqs: ["register", "extract"] },
  { key: "fuse", label: "Fuse Evidence", num: 5, prereqs: ["conflate"] },
  { key: "detect_conflicts", label: "Detect Conflicts", num: 6, prereqs: ["fuse"] },
  { key: "route", label: "Route", num: 7, prereqs: ["detect_conflicts"] },
];

function StageSummary({ stage, data }) {
  if (!data) return null;
  switch (stage) {
    case "ingest":
      return (
        <>
          {data.datasets?.map((d, i) => (
            <div className="metric" key={i}>
              <span>{d.id || `Dataset ${i + 1}`}</span>
              <code>{d.count ?? d.features ?? "—"} features</code>
            </div>
          ))}
          {!data.datasets && data.message && (
            <div className="metric"><span>Info</span><code>{data.message}</code></div>
          )}
        </>
      );
    case "register":
      return (
        <>
          <div className="metric"><span>Method</span><code>{data.method || "affine"}</code></div>
          <div className="metric"><span>RMSE</span><code>{num(data.rmse)} m</code></div>
          {data.residual_count != null && (
            <div className="metric"><span>Residuals</span><code>{data.residual_count}</code></div>
          )}
          {data.p95 != null && (
            <div className="metric"><span>P95</span><code>{num(data.p95)} m</code></div>
          )}
        </>
      );
    case "extract":
      return (
        <>
          <div className="metric"><span>Buildings</span><code>{data.buildings ?? "—"}</code></div>
          <div className="metric"><span>Walls</span><code>{data.walls ?? "—"}</code></div>
          <div className="metric"><span>Total</span><code>{data.total ?? "—"}</code></div>
        </>
      );
    case "conflate":
      return (
        <>
          {data.by_relation
            ? Object.entries(data.by_relation).map(([k, v]) => (
                <div className="metric" key={k}><span>{k}</span><code>{v}</code></div>
              ))
            : <div className="metric"><span>Matches</span><code>{data.match_count ?? "—"}</code></div>
          }
        </>
      );
    case "fuse":
      return (
        <>
          {["geometry", "attribute", "survey", "physical", "temporal", "identity"].map((d) => {
            const key = `${d}_compatibility`;
            const altKey = `${d}_support`;
            const val = data[key] ?? data[altKey];
            return val != null ? (
              <div className="metric" key={d}><span>{d}</span><code>{num(val)}</code></div>
            ) : null;
          })}
        </>
      );
    case "detect_conflicts":
      return (
        <>
          {data.by_type
            ? Object.entries(data.by_type).map(([k, v]) => (
                <div className="metric" key={k}><span>{k}</span><code>{v}</code></div>
              ))
            : <div className="metric"><span>Conflicts</span><code>{data.conflict_count ?? data.count ?? "—"}</code></div>
          }
        </>
      );
    case "route":
      return (
        <>
          <div className="metric"><span>Auto-accept</span><code>{data.auto_accepted ?? "—"}</code></div>
          <div className="metric"><span>Review</span><code>{data.review_count ?? data.review ?? "—"}</code></div>
        </>
      );
    default:
      return null;
  }
}

function num(v) {
  return v != null ? Number(v).toFixed(3) : "—";
}

export default function PipelineStepper({
  pipelineRuns,
  currentRunId,
  onSelectRun,
  onCreateRun,
  onRunStage,
  onRunAll,
  onResetDemo,
  stageStatuses,
  stageResults,
  status,
}) {
  const [expanded, setExpanded] = useState(null);

  const canRun = (stage) => {
    return stage.prereqs.every((p) => stageStatuses[p] === "complete");
  };

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h1>Cadastral Evidence Fusion</h1>
        <p>Pipeline Stepper</p>
      </div>

      <div className="sidebar-actions">
        <button className="primary" onClick={onRunAll} disabled={!currentRunId}>
          ▶ Run All Stages
        </button>
        <button className="danger" onClick={onResetDemo}>
          Reset Demo
        </button>
      </div>

      <div className="pipeline-selector">
        <select
          value={currentRunId || ""}
          onChange={(e) => onSelectRun(e.target.value)}
        >
          <option value="" disabled>Select pipeline run…</option>
          {pipelineRuns.map((r) => (
            <option key={r.id} value={r.id}>
              {r.id} {r.status ? `(${r.status})` : ""}
            </option>
          ))}
        </select>
        <button className="sm" onClick={onCreateRun} title="New pipeline run">+</button>
      </div>

      <div className="stepper">
        {STAGES.map((stage) => {
          const st = stageStatuses[stage.key] || "pending";
          const result = stageResults[stage.key];
          const isExpanded = expanded === stage.key;
          const disabled = !currentRunId || !canRun(stage) || st === "running";

          return (
            <div className="step" key={stage.key}>
              <div className={`step-dot ${st}`} />
              <div className="step-header" onClick={() => setExpanded(isExpanded ? null : stage.key)}>
                <div>
                  <span className="step-number">{stage.num}.</span>
                  <span className="step-title">{stage.label}</span>
                  {st !== "pending" && (
                    <span className={`badge ${st}`} style={{ marginLeft: "0.5rem" }}>
                      {st.toUpperCase()}
                    </span>
                  )}
                </div>
                <button
                  className="sm"
                  disabled={disabled}
                  onClick={(e) => { e.stopPropagation(); onRunStage(stage.key); }}
                >
                  {st === "running" ? <><span className="loading-spinner" />…</> : "Run"}
                </button>
              </div>
              {result && !isExpanded && (
                <div className="step-summary">
                  {stage.key === "register" && result.rmse != null && `RMSE: ${num(result.rmse)} m`}
                  {stage.key === "extract" && result.total != null && `${result.total} observations`}
                  {stage.key === "conflate" && (result.match_count ?? Object.values(result.by_relation || {}).reduce((a, b) => a + b, 0)) + " matches"}
                  {stage.key === "route" && `${result.auto_accepted ?? 0} auto, ${result.review_count ?? result.review ?? 0} review`}
                </div>
              )}
              {isExpanded && (
                <div className="step-details">
                  <StageSummary stage={stage.key} data={result} />
                  {!result && <div className="empty-msg">No results yet</div>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="status-bar">
        {status}
      </div>
    </div>
  );
}
