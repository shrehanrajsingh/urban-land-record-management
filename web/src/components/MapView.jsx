import { useEffect, useRef, useCallback } from "react";
import maplibregl from "maplibre-gl";

const LAYER_STYLES = {
  truth: {
    fill: { "fill-color": "#4caf50", "fill-opacity": 0.12 },
    line: { "line-color": "#4caf50", "line-width": 2 },
  },
  legacy: {
    fill: { "fill-color": "#ef5350", "fill-opacity": 0.08 },
    line: { "line-color": "#ef5350", "line-width": 2, "line-dasharray": [4, 2] },
  },
  survey: {
    fill: { "fill-color": "#42a5f5", "fill-opacity": 0.1 },
    line: { "line-color": "#42a5f5", "line-width": 2 },
  },
  registered: {
    fill: { "fill-color": "#ff9800", "fill-opacity": 0.12 },
    line: { "line-color": "#ff9800", "line-width": 2 },
  },
  observations: {
    fill: { "fill-color": "#ffd54f", "fill-opacity": 0.3 },
    line: { "line-color": "#ffd54f", "line-width": 2 },
  },
  gnss: {
    circle: {
      "circle-radius": 5,
      "circle-color": "#26c6da",
      "circle-stroke-width": 2,
      "circle-stroke-color": "#fff",
    },
  },
  matches: {
    line: { "line-color": "#9e9e9e", "line-width": 1.5, "line-dasharray": [2, 2] },
  },
  conflicts: {
    fill: { "fill-color": "#ef5350", "fill-opacity": 0.3 },
    line: { "line-color": "#ef5350", "line-width": 2.5 },
  },
};

function upsertGeoJson(map, id, data, style) {
  if (!data || data.type !== "FeatureCollection") return;
  if (map.getSource(id)) {
    map.getSource(id).setData(data);
    return;
  }
  map.addSource(id, { type: "geojson", data });
  if (style.fill) {
    map.addLayer({ id: `${id}-fill`, type: "fill", source: id, paint: style.fill });
  }
  if (style.line) {
    map.addLayer({ id: `${id}-line`, type: "line", source: id, paint: style.line });
  }
  if (style.circle) {
    map.addLayer({ id: `${id}-circle`, type: "circle", source: id, paint: style.circle });
  }
}

function setLayerVisibility(map, id, visible) {
  const vis = visible ? "visible" : "none";
  ["fill", "line", "circle"].forEach((suffix) => {
    const layerId = `${id}-${suffix}`;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", vis);
    }
  });
}

export function boundsFromGeoJSON(fc) {
  if (!fc?.features?.length) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const walk = (coords) => {
    if (typeof coords[0] === "number") {
      minX = Math.min(minX, coords[0]);
      minY = Math.min(minY, coords[1]);
      maxX = Math.max(maxX, coords[0]);
      maxY = Math.max(maxY, coords[1]);
      return;
    }
    coords.forEach(walk);
  };
  fc.features.forEach((f) => f.geometry && walk(f.geometry.coordinates));
  if (!Number.isFinite(minX)) return null;
  return [[minX, minY], [maxX, maxY]];
}

export default function MapView({
  layers,
  visibleLayers,
  focusBounds,
  selectedParcelId,
  onParcelClick,
  heatmapData,
  showHeatmap,
}) {
  const ref = useRef(null);
  const mapRef = useRef(null);
  const highlightSourceRef = useRef(false);

  useEffect(() => {
    if (mapRef.current) return;
    const map = new maplibregl.Map({
      container: ref.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap",
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
      center: [77.2, 28.6],
      zoom: 3,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    map.on("click", (e) => {
      const clickableLayers = ["truth-fill", "legacy-fill", "survey-fill", "registered-fill", "conflicts-fill"];
      const existing = clickableLayers.filter((l) => map.getLayer(l));
      if (!existing.length) return;
      const features = map.queryRenderedFeatures(e.point, { layers: existing });
      if (features.length > 0) {
        const f = features[0];
        const id = f.properties?.parcel_id || f.properties?.id || f.id;
        if (id && onParcelClick) onParcelClick(id, f.properties, f.geometry);
      }
    });

    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  const applyLayers = useCallback(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;

    const layerKeys = ["truth", "legacy", "survey", "registered", "observations", "gnss", "matches", "conflicts"];
    layerKeys.forEach((key) => {
      const data = layers[key];
      if (data) {
        upsertGeoJson(map, key, data, LAYER_STYLES[key]);
      }
      setLayerVisibility(map, key, visibleLayers[key] && !!data);
    });

    // Heatmap overlay
    if (heatmapData && showHeatmap) {
      upsertGeoJson(map, "heatmap", heatmapData, {
        fill: { "fill-color": ["interpolate", ["linear"], ["get", "residual"], 0, "#26c6da", 0.5, "#ffd54f", 1, "#ef5350"], "fill-opacity": 0.4 },
        line: { "line-color": "#ffffff", "line-width": 0.5, "line-opacity": 0.3 },
      });
      setLayerVisibility(map, "heatmap", true);
    } else {
      setLayerVisibility(map, "heatmap", false);
    }

    // Highlight selected parcel
    if (selectedParcelId) {
      const allFeatures = [];
      ["truth", "legacy", "survey", "registered"].forEach((k) => {
        if (layers[k]?.features) {
          layers[k].features.forEach((f) => {
            const fid = f.properties?.parcel_id || f.properties?.id;
            if (fid === selectedParcelId) allFeatures.push(f);
          });
        }
      });
      const highlightGeo = { type: "FeatureCollection", features: allFeatures };
      if (map.getSource("highlight")) {
        map.getSource("highlight").setData(highlightGeo);
      } else {
        map.addSource("highlight", { type: "geojson", data: highlightGeo });
        map.addLayer({
          id: "highlight-line",
          type: "line",
          source: "highlight",
          paint: { "line-color": "#ffffff", "line-width": 4, "line-opacity": 0.9 },
        });
        highlightSourceRef.current = true;
      }
    } else if (highlightSourceRef.current && map.getSource("highlight")) {
      map.getSource("highlight").setData({ type: "FeatureCollection", features: [] });
    }
  }, [layers, visibleLayers, selectedParcelId, heatmapData, showHeatmap]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (map.isStyleLoaded()) applyLayers();
    else map.once("load", applyLayers);
  }, [applyLayers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusBounds) return;
    const fit = () => map.fitBounds(focusBounds, { padding: 48, duration: 800 });
    if (map.isStyleLoaded()) fit();
    else map.once("load", fit);
  }, [focusBounds]);

  return <div id="map" ref={ref} />;
}
