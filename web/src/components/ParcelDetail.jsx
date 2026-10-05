export default function ParcelDetail({ parcelId, properties, onClose }) {
  if (!parcelId) return null;

  const props = properties || {};

  return (
    <div className="detail-section">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3>Parcel: {parcelId}</h3>
        {onClose && (
          <button className="detail-close" onClick={onClose}>✕</button>
        )}
      </div>
      <div className="card">
        {Object.entries(props).map(([k, v]) => (
          <div className="metric" key={k}>
            <span>{k.replace(/_/g, " ")}</span>
            <code>{v != null ? String(v) : "—"}</code>
          </div>
        ))}
        {Object.keys(props).length === 0 && (
          <div className="empty-msg">No attribute data available</div>
        )}
      </div>
    </div>
  );
}
