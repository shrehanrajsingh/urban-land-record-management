import { useState, useEffect } from "react";
import { api } from "../api.js";

export default function HistoryTimeline({ parcelId }) {
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState(null);

  useEffect(() => {
    if (!parcelId) return;
    setLoading(true);
    api.getParcelVersions(parcelId)
      .then((v) => {
        const list = Array.isArray(v) ? v : v?.versions || [];
        setVersions(list);
        setSelectedVersion(null);
      })
      .catch(() => setVersions([]))
      .finally(() => setLoading(false));
  }, [parcelId]);

  if (!parcelId) {
    return (
      <div className="card">
        <h3>Version History</h3>
        <div className="empty-msg">Select a parcel to view history</div>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>History — {parcelId}</h3>
      {loading && <div><span className="loading-spinner" />Loading…</div>}

      {!loading && versions.length === 0 && (
        <div className="empty-msg">No version history available</div>
      )}

      <div className="timeline">
        {versions.map((v, i) => {
          const status = (v.status || "OBSERVED").toLowerCase();
          const isSelected = selectedVersion === i;
          return (
            <div
              key={i}
              className={`timeline-node ${status}`}
              onClick={() => setSelectedVersion(isSelected ? null : i)}
              style={{ cursor: "pointer" }}
            >
              <div className="timeline-version">
                v{v.version ?? i + 1} — <span className={`badge ${status === "observed" ? "pending" : status === "candidate" ? "review" : status === "validated" ? "strong" : "complete"}`}>
                  {(v.status || "OBSERVED").toUpperCase()}
                </span>
              </div>
              <div className="timeline-meta">
                {v.created_at || v.timestamp || "—"}
                {v.created_by && ` · ${v.created_by}`}
              </div>

              {isSelected && (
                <div style={{
                  marginTop: "0.4rem",
                  padding: "0.5rem",
                  background: "#f8f9fa",
                  borderRadius: "6px",
                  fontSize: "0.74rem",
                  fontFamily: "'IBM Plex Mono', monospace",
                  whiteSpace: "pre-wrap",
                  color: "#555",
                }}>
                  {v.changes
                    ? JSON.stringify(v.changes, null, 2)
                    : v.geometry_wkt
                      ? `Geometry: ${v.geometry_wkt.substring(0, 120)}…`
                      : "No diff data available"
                  }
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
