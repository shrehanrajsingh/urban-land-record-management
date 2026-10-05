import { useState } from "react";
import EvidencePanel from "./EvidencePanel.jsx";

const AUTO_ACCEPT_CONDITIONS = [
  "Geometry compatibility ≥ 0.85",
  "Attribute compatibility ≥ 0.85",
  "Survey support ≥ 0.5",
  "Physical support ≥ 0.3",
  "Temporal support ≥ 0.5",
  "Identity compatibility ≥ 0.8",
  "Match probability ≥ 0.95",
  "No active conflicts",
  "ONE_TO_ONE relation type",
];

function evalAutoAccept(detail) {
  const ev = detail?.match?.evidence || detail?.evidence_snapshot || {};
  const prob = detail?.match?.match_probability;
  const rel = detail?.match?.relation_type;
  const hasConflict = !!detail?.conflict;
  return [
    (ev.geometry_compatibility ?? 0) >= 0.85,
    (ev.attribute_compatibility ?? 0) >= 0.85,
    (ev.survey_support ?? 0) >= 0.5,
    (ev.physical_support ?? 0) >= 0.3,
    (ev.temporal_support ?? 0) >= 0.5,
    (ev.identity_compatibility ?? 0) >= 0.8,
    (prob ?? 0) >= 0.95,
    !hasConflict,
    rel === "ONE_TO_ONE",
  ];
}

export default function ReviewWorkspace({
  cases,
  selectedCaseId,
  onSelectCase,
  caseDetail,
  onSubmitAction,
  loading,
}) {
  const [expandedCase, setExpandedCase] = useState(null);
  const [reason, setReason] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  const handleAction = async (action) => {
    if (!selectedCaseId) return;
    setActionLoading(true);
    try {
      await onSubmitAction(selectedCaseId, action, reason);
      setReason("");
    } finally {
      setActionLoading(false);
    }
  };

  const autoChecks = caseDetail ? evalAutoAccept(caseDetail) : [];
  const ev = caseDetail?.match?.evidence || caseDetail?.evidence_snapshot || {};

  return (
    <div style={{ padding: "0.85rem 1rem" }}>
      <h3 style={{ fontSize: "0.88rem", marginBottom: "0.6rem" }}>Review Queue</h3>

      {loading && <div><span className="loading-spinner" />Loading…</div>}

      {!loading && cases.length === 0 && (
        <div className="empty-msg">No review cases — run the pipeline first.</div>
      )}

      <ul className="review-list">
        {cases.map((c) => {
          const isActive = c.id === selectedCaseId;
          const isExpanded = expandedCase === c.id && isActive;

          return (
            <li
              key={c.id}
              className={`review-item ${isActive ? "active" : ""}`}
              onClick={() => {
                onSelectCase(c.id);
                setExpandedCase(isActive && isExpanded ? null : c.id);
              }}
            >
              <div className="review-item-header">
                <strong>{c.id}</strong>
                <span className={`badge ${c.status === "OPEN" ? "review" : c.status === "RESOLVED" ? "complete" : "pending"}`}>
                  {c.status || "OPEN"}
                </span>
              </div>
              <div className="review-item-summary">{c.summary || "—"}</div>

              {isExpanded && caseDetail && (
                <div style={{ marginTop: "0.6rem" }} onClick={(e) => e.stopPropagation()}>
                  <EvidencePanel
                    evidence={ev}
                    matchProbability={caseDetail.match?.match_probability}
                    uncertaintyM={caseDetail.registration?.uncertainty_m}
                    relationType={caseDetail.match?.relation_type}
                  />

                  {caseDetail.conflict && (
                    <div className="alert-box error" style={{ marginTop: "0.5rem" }}>
                      <strong>{caseDetail.conflict.type}</strong>: {caseDetail.conflict.description}
                    </div>
                  )}

                  <div style={{ marginTop: "0.6rem" }}>
                    <h3 style={{ fontSize: "0.8rem", marginBottom: "0.35rem" }}>Auto-Accept Checklist</h3>
                    <ul className="checklist">
                      {AUTO_ACCEPT_CONDITIONS.map((cond, i) => (
                        <li key={i}>
                          <span className={autoChecks[i] ? "check-icon" : "cross-icon"}>
                            {autoChecks[i] ? "✓" : "✗"}
                          </span>
                          {cond}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div style={{ marginTop: "0.65rem" }}>
                    <input
                      className="reason-input"
                      placeholder="Reason / notes…"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                    <div className="review-actions">
                      <button className="success sm" disabled={actionLoading} onClick={() => handleAction("ACCEPT")}>
                        Accept
                      </button>
                      <button className="danger sm" disabled={actionLoading} onClick={() => handleAction("REJECT")}>
                        Reject
                      </button>
                      <button className="warn sm" disabled={actionLoading} onClick={() => handleAction("REFER_EXPERT")}>
                        Refer Expert
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
