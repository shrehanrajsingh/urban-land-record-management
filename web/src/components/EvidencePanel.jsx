const DIMS = [
  { key: "geometry_compatibility", label: "Geometry", color: "#4caf50" },
  { key: "attribute_compatibility", label: "Attribute", color: "#42a5f5" },
  { key: "survey_support", label: "Survey", color: "#26c6da" },
  { key: "physical_support", label: "Physical", color: "#ffd54f" },
  { key: "temporal_support", label: "Temporal", color: "#ff9800" },
  { key: "identity_compatibility", label: "Identity", color: "#ab47bc" },
];

function RadarChart({ evidence, size = 180 }) {
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 20;

  const angleStep = (2 * Math.PI) / DIMS.length;
  const offset = -Math.PI / 2;

  const gridLevels = [0.25, 0.5, 0.75, 1.0];

  const points = DIMS.map((d, i) => {
    const val = evidence[d.key] ?? 0;
    const angle = offset + i * angleStep;
    return {
      x: cx + r * val * Math.cos(angle),
      y: cy + r * val * Math.sin(angle),
      label: d.label,
      lx: cx + (r + 14) * Math.cos(angle),
      ly: cy + (r + 14) * Math.sin(angle),
      color: d.color,
      val,
    };
  });

  const polygon = points.map((p) => `${p.x},${p.y}`).join(" ");

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ display: "block", margin: "0 auto" }}>
      {gridLevels.map((level) => (
        <polygon
          key={level}
          points={DIMS.map((_, i) => {
            const a = offset + i * angleStep;
            return `${cx + r * level * Math.cos(a)},${cy + r * level * Math.sin(a)}`;
          }).join(" ")}
          fill="none"
          stroke="#e0e0e0"
          strokeWidth="0.5"
        />
      ))}
      {DIMS.map((_, i) => {
        const a = offset + i * angleStep;
        return (
          <line
            key={i}
            x1={cx} y1={cy}
            x2={cx + r * Math.cos(a)} y2={cy + r * Math.sin(a)}
            stroke="#e0e0e0"
            strokeWidth="0.5"
          />
        );
      })}
      <polygon points={polygon} fill="rgba(79, 195, 247, 0.25)" stroke="#4fc3f7" strokeWidth="2" />
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r="3.5" fill={p.color} stroke="#fff" strokeWidth="1" />
          <text
            x={p.lx} y={p.ly}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize="8"
            fill="#6b7280"
            fontFamily="DM Sans, sans-serif"
          >
            {p.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

export default function EvidencePanel({ evidence, matchProbability, uncertaintyM, relationType }) {
  if (!evidence || Object.keys(evidence).length === 0) {
    return (
      <div className="card">
        <h3>Evidence</h3>
        <div className="empty-msg">Select a match or parcel to view evidence</div>
      </div>
    );
  }

  const prob = matchProbability != null ? Number(matchProbability) : null;
  const probClass = prob != null ? (prob > 0.9 ? "high" : prob > 0.7 ? "mid" : "low") : "";

  return (
    <div className="card">
      <h3>Evidence Analysis</h3>

      {prob != null && (
        <div className="match-prob">
          <div className={`match-prob-value ${probClass}`}>
            {(prob * 100).toFixed(1)}%
          </div>
          <div className="match-prob-label">Match Probability</div>
        </div>
      )}

      {relationType && (
        <div style={{ textAlign: "center", marginBottom: "0.6rem" }}>
          <span className="badge-relation">{relationType}</span>
        </div>
      )}

      <RadarChart evidence={evidence} />

      <div className="evidence-bars" style={{ marginTop: "0.75rem" }}>
        {DIMS.map((d) => {
          const val = evidence[d.key] ?? 0;
          return (
            <div className="evidence-bar-row" key={d.key}>
              <span className="evidence-bar-label">{d.label}</span>
              <div className="evidence-bar-track">
                <div
                  className="evidence-bar-fill"
                  style={{
                    width: `${val * 100}%`,
                    background: `linear-gradient(90deg, ${d.color}88, ${d.color})`,
                  }}
                />
              </div>
              <span className="evidence-bar-value">{val.toFixed(2)}</span>
            </div>
          );
        })}
      </div>

      {uncertaintyM != null && (
        <div className="metric" style={{ marginTop: "0.6rem" }}>
          <span>Uncertainty envelope</span>
          <code>{Number(uncertaintyM).toFixed(2)} m</code>
        </div>
      )}
    </div>
  );
}
