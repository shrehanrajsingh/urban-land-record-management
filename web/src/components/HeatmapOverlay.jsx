export default function HeatmapOverlay({ visible }) {
  if (!visible) return null;

  return (
    <div className="heatmap-legend">
      <div style={{ fontWeight: 600, marginBottom: "0.3rem" }}>Registration Residuals</div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
        <span style={{ fontSize: "0.68rem" }}>0 m</span>
        <div style={{
          flex: 1,
          height: 10,
          borderRadius: 5,
          background: "linear-gradient(90deg, #26c6da, #ffd54f, #ef5350)",
        }} />
        <span style={{ fontSize: "0.68rem" }}>1+ m</span>
      </div>
    </div>
  );
}
