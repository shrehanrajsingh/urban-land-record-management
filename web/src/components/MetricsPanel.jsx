export default function MetricsPanel({ registration, modelRuns, benchmark }) {
  const reg = registration || {};
  const runs = Array.isArray(modelRuns) ? modelRuns : [];
  const bench = benchmark || {};
  const latestRun = runs.length > 0 ? runs[runs.length - 1] : null;

  return (
    <div style={{ padding: "0.85rem 1rem" }}>
      <h3 style={{ fontSize: "0.88rem", marginBottom: "0.6rem" }}>Metrics</h3>

      <div className="card">
        <h3>Registration</h3>
        <div className="metric">
          <span>Method</span>
          <code>{reg.method || "—"}</code>
        </div>
        <div className="metric">
          <span>Global RMSE</span>
          <code>{reg.global_rmse != null ? `${Number(reg.global_rmse).toFixed(4)} m` : "—"}</code>
        </div>
        <div className="metric">
          <span>P95</span>
          <code>{reg.p95 != null ? `${Number(reg.p95).toFixed(4)} m` : "—"}</code>
        </div>
        {reg.regional_breakdown && (
          <div style={{ marginTop: "0.5rem" }}>
            <table className="metrics-table">
              <thead>
                <tr><th>Region</th><th>RMSE</th><th>Count</th></tr>
              </thead>
              <tbody>
                {(Array.isArray(reg.regional_breakdown) ? reg.regional_breakdown : Object.entries(reg.regional_breakdown).map(([k, v]) => ({ region: k, ...v }))).map((r, i) => (
                  <tr key={i}>
                    <td>{r.region || r.id || i + 1}</td>
                    <td>{r.rmse != null ? Number(r.rmse).toFixed(4) : "—"}</td>
                    <td>{r.count ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h3>Matching Model</h3>
        {latestRun ? (
          <>
            <div className="metric">
              <span>Precision</span>
              <code>{latestRun.precision != null ? Number(latestRun.precision).toFixed(3) : "—"}</code>
            </div>
            <div className="metric">
              <span>Recall</span>
              <code>{latestRun.recall != null ? Number(latestRun.recall).toFixed(3) : "—"}</code>
            </div>
            <div className="metric">
              <span>Brier Score</span>
              <code>{latestRun.brier_score != null ? Number(latestRun.brier_score).toFixed(4) : "—"}</code>
            </div>
            <div className="metric">
              <span>Run ID</span>
              <code>{latestRun.id || "—"}</code>
            </div>
          </>
        ) : (
          <div className="empty-msg">No model runs available</div>
        )}
      </div>

      <div className="card">
        <h3>Benchmark</h3>
        <div className="metric">
          <span>Conflation Precision</span>
          <code>{bench.conflation_precision != null ? Number(bench.conflation_precision).toFixed(3) : "—"}</code>
        </div>
        <div className="metric">
          <span>Conflict Recall</span>
          <code>{bench.conflict_recall != null ? Number(bench.conflict_recall).toFixed(3) : "—"}</code>
        </div>
        <div className="metric">
          <span>Silent Error Rate</span>
          <code>{bench.silent_error_rate != null ? Number(bench.silent_error_rate).toFixed(4) : "—"}</code>
        </div>
      </div>
    </div>
  );
}
