const LAYER_DEFS = [
  { key: "truth", label: "Truth", color: "#4caf50" },
  { key: "legacy", label: "Legacy", color: "#ef5350" },
  { key: "survey", label: "Survey", color: "#42a5f5" },
  { key: "registered", label: "Registered Legacy", color: "#ff9800" },
  { key: "observations", label: "Observations", color: "#ffd54f" },
  { key: "gnss", label: "GNSS", color: "#26c6da" },
  { key: "matches", label: "Matches", color: "#9e9e9e" },
  { key: "conflicts", label: "Conflicts", color: "#ef5350" },
];

export default function LayerToggles({ visible, onToggle, showHeatmap, onToggleHeatmap }) {
  return (
    <div className="layer-toggles">
      {LAYER_DEFS.map((l) => (
        <label className="layer-toggle" key={l.key}>
          <input
            type="checkbox"
            checked={!!visible[l.key]}
            onChange={() => onToggle(l.key)}
          />
          <span className="layer-swatch" style={{ background: l.color }} />
          {l.label}
        </label>
      ))}
      <hr style={{ border: "none", borderTop: "1px solid #2a2a4a", margin: "0.3rem 0" }} />
      <label className="layer-toggle">
        <input
          type="checkbox"
          checked={!!showHeatmap}
          onChange={onToggleHeatmap}
        />
        <span className="layer-swatch" style={{ background: "linear-gradient(90deg, #26c6da, #ffd54f, #ef5350)", borderRadius: 2 }} />
        Residual Heatmap
      </label>
    </div>
  );
}
