import { useState, useEffect } from "react";
import { api } from "../api.js";

const STATES = [
  { id: "OBSERVED", x: 30, y: 25 },
  { id: "CANDIDATE", x: 160, y: 25 },
  { id: "VALIDATED", x: 290, y: 25 },
  { id: "APPROVED", x: 420, y: 25 },
  { id: "PUBLISHED", x: 550, y: 25 },
];

const ARROWS = [
  { from: 0, to: 1, label: "" },
  { from: 1, to: 2, label: "" },
  { from: 2, to: 3, label: "" },
  { from: 3, to: 4, label: "", restricted: true },
];

function StateMachineDiagram() {
  const w = 640;
  const h = 80;
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block", marginBottom: "0.5rem" }}>
      {ARROWS.map((a, i) => {
        const from = STATES[a.from];
        const to = STATES[a.to];
        return (
          <g key={i}>
            <line
              x1={from.x + 50} y1={from.y + 12}
              x2={to.x - 5} y2={to.y + 12}
              stroke={a.restricted ? "#ef5350" : "#9e9e9e"}
              strokeWidth={a.restricted ? 2.5 : 1.5}
              markerEnd="url(#arrowhead)"
            />
            {a.restricted && (
              <text
                x={(from.x + 50 + to.x - 5) / 2}
                y={from.y + 5}
                textAnchor="middle"
                fontSize="7"
                fill="#ef5350"
                fontWeight="bold"
              >
                AI_SERVICE ✗
              </text>
            )}
          </g>
        );
      })}
      <defs>
        <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="#9e9e9e" />
        </marker>
      </defs>
      {STATES.map((s, i) => {
        const isRestricted = s.id === "PUBLISHED";
        return (
          <g key={i}>
            <rect
              x={s.x - 5}
              y={s.y}
              width={i === STATES.length - 1 ? 85 : 75}
              height={24}
              rx="4"
              fill={isRestricted ? "#fde8e8" : "#e8f5e9"}
              stroke={isRestricted ? "#ef5350" : "#4caf50"}
              strokeWidth="1.5"
            />
            <text
              x={s.x + (i === STATES.length - 1 ? 37 : 32)}
              y={s.y + 16}
              textAnchor="middle"
              fontSize="8"
              fontWeight="600"
              fill={isRestricted ? "#c62828" : "#2e7d32"}
              fontFamily="DM Sans, sans-serif"
            >
              {s.id}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export default function GovernancePanel({ onStatusUpdate }) {
  const [aiResult, setAiResult] = useState(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [auditEvents, setAuditEvents] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);

  useEffect(() => {
    setAuditLoading(true);
    api.auditEvents()
      .then((events) => setAuditEvents(Array.isArray(events) ? events : []))
      .catch(() => setAuditEvents([]))
      .finally(() => setAuditLoading(false));
  }, []);

  const tryAiPublish = async () => {
    setAiLoading(true);
    setAiResult(null);
    try {
      const r = await api.aiPublish("P-103");
      setAiResult(r);
      if (onStatusUpdate) {
        onStatusUpdate(r.rejected
          ? "DB/app rejected AI → PUBLISHED (invariant held)"
          : "UNEXPECTED: AI publish succeeded"
        );
      }
    } catch (e) {
      setAiResult({ error: e.message });
    } finally {
      setAiLoading(false);
    }
  };

  const refreshAudit = () => {
    setAuditLoading(true);
    api.auditEvents()
      .then((events) => setAuditEvents(Array.isArray(events) ? events : []))
      .catch(() => setAuditEvents([]))
      .finally(() => setAuditLoading(false));
  };

  return (
    <div style={{ padding: "0.85rem 1rem" }}>
      <h3 style={{ fontSize: "0.88rem", marginBottom: "0.6rem" }}>Governance — TC15</h3>

      <div className="card">
        <h3>State Machine</h3>
        <StateMachineDiagram />
        <p style={{ fontSize: "0.74rem", color: "#6b7280", margin: "0.3rem 0 0" }}>
          AI_SERVICE actors cannot transition parcels to PUBLISHED — this is enforced at the DB trigger level.
        </p>
      </div>

      <div className="card">
        <h3>TC15 Demo: AI → Publish</h3>
        <p style={{ fontSize: "0.78rem", color: "#6b7280", marginBottom: "0.5rem" }}>
          Attempt to publish a parcel via AI_SERVICE actor. The database trigger should reject this transition.
        </p>
        <button
          className="danger"
          onClick={tryAiPublish}
          disabled={aiLoading}
          style={{ width: "100%" }}
        >
          {aiLoading ? <><span className="loading-spinner" />Testing…</> : "Try AI → Publish"}
        </button>

        {aiResult && (
          <div className={`alert-box ${aiResult.rejected || aiResult.error ? "error" : "success"}`} style={{ marginTop: "0.6rem" }}>
            {aiResult.rejected && (
              <>
                <strong>✓ Correctly Rejected</strong>
                <div style={{ marginTop: "0.3rem", fontSize: "0.76rem" }}>
                  {aiResult.message || aiResult.detail || "AI_SERVICE cannot publish parcels — governance invariant held."}
                </div>
              </>
            )}
            {aiResult.error && (
              <>
                <strong>Rejection / Error</strong>
                <div style={{ marginTop: "0.3rem", fontSize: "0.76rem" }}>{aiResult.error}</div>
              </>
            )}
            {!aiResult.rejected && !aiResult.error && (
              <>
                <strong>⚠ Unexpected Success</strong>
                <div style={{ marginTop: "0.3rem", fontSize: "0.76rem" }}>
                  AI publish was not rejected — governance invariant may be broken!
                </div>
              </>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3>Audit Log</h3>
          <button className="sm" onClick={refreshAudit} disabled={auditLoading}>Refresh</button>
        </div>
        {auditLoading && <div><span className="loading-spinner" />Loading…</div>}
        <div className="audit-log">
          {auditEvents.length === 0 && !auditLoading && (
            <div className="empty-msg">No audit events recorded</div>
          )}
          {auditEvents.map((ev, i) => (
            <div className="audit-entry" key={i}>
              <span>
                <strong>{ev.parcel_id || ev.entity_id || "—"}</strong>{" "}
                <span style={{ color: "#6b7280" }}>{ev.action || ev.event_type || "—"}</span>
              </span>
              <span className={`badge ${ev.result === "REJECTED" || ev.result === "FAILED" ? "conflict" : "pass"}`}>
                {ev.result || ev.status || "—"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
