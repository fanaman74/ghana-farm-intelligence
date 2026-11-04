import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import maplibregl, { type Map as MLMap, type Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Farm, Geometry, LayerState, MapOverlay } from "../types";

export type MapMode = "idle" | "draw" | "edit" | "split" | "gps";

export interface MapApi {
  finishDraw: () => void;
  finishGps: () => Geometry | null;
  undo: () => void;
  redo: () => void;
  cancel: () => void;
  loadGeometry: (geometry: Geometry) => void;
  getDraft: () => Geometry | null;
  fitFarm: (geometry: Geometry, options?: { paddingBottom?: number }) => void;
  fitSatellite: (corners: number[][]) => void;
  flyTo: (lat: number, lon: number, zoom?: number) => void;
  setTerrain: (enabled: boolean) => void;
  resetSplit: () => void;
  map: () => MLMap | null;
}

export interface DraftStats {
  areaHa: number;
  perimeterM: number;
  vertices: number;
}

interface Props {
  farms: Farm[];
  selectedFarmId: string | null;
  farm: Farm | null;
  overlays: MapOverlay[];
  satellite: boolean;
  mode: MapMode;
  layers: LayerState[];
  theme: "dark" | "daylight";
  onSelectFarm: (farmId: string) => void;
  onDraftChange: (geometry: Geometry | null, stats: DraftStats | null) => void;
  onDraftComplete: (geometry: Geometry) => void;
  onSplit: (line: Geometry) => void;
  onGpsStatus: (status: { active: boolean; points: number; accuracy: number | null; error?: string }) => void;
  onMapClick: (lat: number, lon: number) => void;
}

const GHANA_CENTER: [number, number] = [-1.02, 7.95];
const SATELLITE_TILES =
  "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg";
const SATELLITE_ATTRIBUTION =
  "Sentinel-2 cloudless 2025 by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2025), CC BY-SA 4.0";
const EARTH_RADIUS = 6371008.8;

function ringAreaHa(ring: number[][]): number {
  if (ring.length < 3) return 0;
  let total = 0;
  for (let index = 0; index < ring.length; index += 1) {
    const [lon1, lat1] = ring[index];
    const [lon2, lat2] = ring[(index + 1) % ring.length];
    total +=
      ((lon2 - lon1) * Math.PI) / 180 *
      (2 + Math.sin((lat1 * Math.PI) / 180) + Math.sin((lat2 * Math.PI) / 180));
  }
  return Math.abs((total * EARTH_RADIUS * EARTH_RADIUS) / 2) / 10000;
}

function ringPerimeterM(ring: number[][]): number {
  let total = 0;
  for (let index = 0; index < ring.length; index += 1) {
    const [lon1, lat1] = ring[index];
    const [lon2, lat2] = ring[(index + 1) % ring.length];
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLon = ((lon2 - lon1) * Math.PI) / 180;
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
    total += 2 * EARTH_RADIUS * Math.asin(Math.min(1, Math.sqrt(a)));
  }
  return total;
}

function polygonGeometry(ring: number[][]): Geometry {
  const closed = [...ring];
  if (closed.length && (closed[0][0] !== closed[closed.length - 1][0] || closed[0][1] !== closed[closed.length - 1][1])) {
    closed.push([...closed[0]]);
  }
  return { type: "Polygon", coordinates: [closed] };
}

function bboxOfGeometry(geometry: Geometry): [[number, number], [number, number]] {
  let west = 180;
  let south = 90;
  let east = -180;
  let north = -90;
  const walk = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (typeof value[0] === "number" && typeof value[1] === "number") {
      const [lon, lat] = value as number[];
      west = Math.min(west, lon);
      east = Math.max(east, lon);
      south = Math.min(south, lat);
      north = Math.max(north, lat);
      return;
    }
    value.forEach(walk);
  };
  walk(geometry.coordinates);
  return [
    [west, south],
    [east, north],
  ];
}

function buildStyle(theme: "dark" | "daylight"): maplibregl.StyleSpecification {
  const dark = theme === "dark";
  const palette = dark
    ? {
        background: "#0b110e",
        land: "#131c17",
        landAlt: "#17211b",
        border: "#28382f",
        district: "#1d2b24",
        water: "#0e2434",
        waterLine: "#17374d",
        roadCasing: "#0a0f0c",
        roadMajor: "#3c4d42",
        roadMinor: "#2b3a31",
      }
    : {
        background: "#f4f1e6",
        land: "#eeeade",
        landAlt: "#f7f5ec",
        border: "#c9c3ae",
        district: "#dcd7c6",
        water: "#b9d6e8",
        waterLine: "#8ebbd6",
        roadCasing: "#d9d3c0",
        roadMajor: "#ffffff",
        roadMinor: "#f6f3e8",
      };

  return {
    version: 8,
    name: "gfi-local",
    sources: {
      regions: { type: "geojson", data: "/api/geo/boundaries/ADM1" },
      districts: { type: "geojson", data: "/api/geo/boundaries/ADM2" },
      roads: { type: "geojson", data: "/api/basemap/roads" },
      water: { type: "geojson", data: "/api/basemap/water" },
      farms: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      selected: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      draft: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      vertices: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      split: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      gps: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": palette.background } },
      {
        id: "region-fill",
        type: "fill",
        source: "regions",
        paint: { "fill-color": palette.land, "fill-opacity": 1 },
      },
      {
        id: "region-line",
        type: "line",
        source: "regions",
        paint: { "line-color": palette.border, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.6, 10, 1.2] },
      },
      {
        id: "district-line",
        type: "line",
        source: "districts",
        minzoom: 7.5,
        paint: {
          "line-color": palette.district,
          "line-width": ["interpolate", ["linear"], ["zoom"], 7.5, 0.4, 12, 0.9],
          "line-dasharray": [2, 1.5],
        },
      },
      {
        id: "water-fill",
        type: "fill",
        source: "water",
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": palette.water, "fill-opacity": 0.95 },
      },
      {
        id: "water-line",
        type: "line",
        source: "water",
        filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": palette.waterLine, "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.5, 12, 1.6] },
      },
      {
        id: "roads-casing-major",
        type: "line",
        source: "roads",
        minzoom: 6,
        filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"]]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": palette.roadCasing,
          "line-width": ["interpolate", ["linear"], ["zoom"], 6, 1.4, 10, 3.4, 14, 7],
        },
      },
      {
        id: "roads-main-major",
        type: "line",
        source: "roads",
        minzoom: 6,
        filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"]]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": palette.roadMajor,
          "line-width": ["interpolate", ["linear"], ["zoom"], 6, 1.0, 10, 2.6, 14, 5.4],
        },
      },
      {
        id: "roads-casing-minor",
        type: "line",
        source: "roads",
        minzoom: 8.5,
        filter: ["in", ["get", "class"], ["literal", ["secondary", "tertiary", "secondary_link", "tertiary_link"]]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": palette.roadCasing,
          "line-width": ["interpolate", ["linear"], ["zoom"], 8.5, 1.0, 12, 2.8, 15, 5],
        },
      },
      {
        id: "roads-main-minor",
        type: "line",
        source: "roads",
        minzoom: 8.5,
        filter: ["in", ["get", "class"], ["literal", ["secondary", "tertiary", "secondary_link", "tertiary_link"]]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": palette.roadMinor,
          "line-width": ["interpolate", ["linear"], ["zoom"], 8.5, 0.6, 12, 1.8, 15, 3.2],
        },
      },
      {
        id: "farms-fill",
        type: "fill",
        source: "farms",
        paint: { "fill-color": "#3fa88f", "fill-opacity": 0.14 },
      },
      {
        id: "farms-line",
        type: "line",
        source: "farms",
        paint: { "line-color": "#3fa88f", "line-width": 1.4, "line-dasharray": [3, 2] },
      },
      {
        id: "selected-fill",
        type: "fill",
        source: "selected",
        paint: { "fill-color": "#e0a33e", "fill-opacity": 0.22 },
      },
      {
        id: "selected-line",
        type: "line",
        source: "selected",
        paint: { "line-color": "#f0b64f", "line-width": 3 },
      },
      {
        id: "draft-fill",
        type: "fill",
        source: "draft",
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": "#e0a33e", "fill-opacity": 0.18 },
      },
      {
        id: "draft-line",
        type: "line",
        source: "draft",
        paint: { "line-color": "#f0b64f", "line-width": 2, "line-dasharray": [2, 1] },
      },
      {
        id: "gps-line",
        type: "line",
        source: "gps",
        paint: { "line-color": "#3fa88f", "line-width": 2.5 },
      },
      {
        id: "vertices",
        type: "circle",
        source: "vertices",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 5, 18, 8],
          "circle-color": "#f7f4e8",
          "circle-stroke-color": "#1b2b22",
          "circle-stroke-width": 2,
        },
      },
      {
        id: "split-line",
        type: "line",
        source: "split",
        paint: { "line-color": "#d9694f", "line-width": 2.5, "line-dasharray": [2, 1] },
      },
    ],
  } as maplibregl.StyleSpecification;
}

function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

export const MapView = forwardRef<MapApi, Props>(function MapView(props, ref) {
  const {
    farms,
    selectedFarmId,
    farm,
    overlays,
    satellite,
    mode,
    layers,
    theme,
    onSelectFarm,
    onDraftChange,
    onDraftComplete,
    onSplit,
    onGpsStatus,
    onMapClick,
  } = props;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const modeRef = useRef<MapMode>(mode);
  const draftRef = useRef<number[][]>([]);
  const redoRef = useRef<number[][]>([]);
  const vertexMarkers = useRef<Marker[]>([]);
  const splitPoints = useRef<number[][]>([]);
  const gpsWatch = useRef<number | null>(null);
  const gpsPoints = useRef<number[][]>([]);
  const overlayIds = useRef<Set<string>>(new Set());
  const overlayUrls = useRef<Record<string, string>>({});
  const labelMarkers = useRef<Marker[]>([]);
  const labelTimer = useRef<number | null>(null);
  const labelSignature = useRef<string>("");
  const fittedFarmId = useRef<string | null>(null);
  const placesRef = useRef<Array<{ name: string; type: string; lat: number; lon: number; region?: string }>>([]);
  const adminLabels = useRef<Array<{ name: string; lat: number; lon: number; level: number }>>([]);
  const callbacksRef = useRef({ onDraftChange, onDraftComplete, onSplit, onGpsStatus, onMapClick, onSelectFarm });
  callbacksRef.current = { onDraftChange, onDraftComplete, onSplit, onGpsStatus, onMapClick, onSelectFarm };
  modeRef.current = mode;

  // ------------------------------------------------------------------ map init
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(theme),
      center: GHANA_CENTER,
      zoom: 6.4,
      minZoom: 3,
      maxZoom: 19,
      // A loose regional box: the old Ghana-only clamp stopped zoom-out and
      // panning at a hard edge while you were trying to find your field.
      maxBounds: [
        [-20, -12],
        [18, 26],
      ],
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-left");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 90 }), "bottom-left");
    map.addControl(
      new maplibregl.AttributionControl({
        compact: true,
        customAttribution:
          "© OpenStreetMap contributors (ODbL) · geoBoundaries · ISRIC SoilGrids (CC BY 4.0)",
      }),
      "bottom-right",
    );

    map.on("load", () => {
      setMapReady(true);
      map.addSource("places-circle", { type: "geojson", data: emptyCollection() });
      map.addLayer({
        id: "places-circle",
        type: "circle",
        source: "places-circle",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 1.6, 12, 4],
          "circle-color": theme === "dark" ? "#c8d3c9" : "#4a5449",
          "circle-opacity": 0.85,
        },
      });
      map.on("moveend", () => scheduleLabels());
      map.on("zoomend", () => scheduleLabels());
      map.on("idle", () => scheduleLabels(260));
    });

    map.on("click", (event) => {
      handleMapClick(event.lngLat.lat, event.lngLat.lng);
    });
    map.on("click", "farms-fill", (event) => {
      if (modeRef.current !== "idle") return;
      const feature = event.features?.[0];
      const id = feature?.properties?.id as string | undefined;
      if (id) callbacksRef.current.onSelectFarm(id);
    });
    map.on("mouseenter", "farms-fill", () => {
      if (modeRef.current === "idle") map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "farms-fill", () => {
      if (modeRef.current === "idle") map.getCanvas().style.cursor = "";
    });
    map.on("style.load", () => renderLabels());

    void loadReferencedData(map);
    loadPlaces();
    return () => {
      stopGps();
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadPlaces() {
    try {
      const response = await fetch("/api/basemap/places?limit=3500");
      if (!response.ok) return;
      const payload = await response.json();
      placesRef.current = (payload.features ?? []).map((feature: any) => ({
        name: feature.properties.name,
        type: feature.properties.type,
        lat: feature.geometry.coordinates[1],
        lon: feature.geometry.coordinates[0],
        region: feature.properties.region,
      }));
      const map = mapRef.current;
      const source = map?.getSource("places-circle") as maplibregl.GeoJSONSource | undefined;
      if (source && payload.features) {
        source.setData({ type: "FeatureCollection", features: payload.features });
      }
      renderLabels(true);
    } catch (error) {
      console.warn("Place labels unavailable", error);
    }
  }

  async function loadReferencedData(map: MLMap) {
    try {
      const response = await fetch("/api/geo/boundaries/ADM2");
      if (!response.ok) return;
      const payload = await response.json();
      adminLabels.current = (payload.features ?? [])
        .map((feature: any) => {
          const coords = feature.geometry.coordinates;
          const ring = feature.geometry.type === "Polygon" ? coords[0] : coords[0][0];
          let west = 180;
          let east = -180;
          let south = 90;
          let north = -90;
          for (const point of ring) {
            if (point[0] < west) west = point[0];
            if (point[0] > east) east = point[0];
            if (point[1] < south) south = point[1];
            if (point[1] > north) north = point[1];
          }
          return {
            name: feature.properties.name,
            lon: (west + east) / 2,
            lat: (south + north) / 2,
            level: 6,
          };
        })
        .filter((label: { lon: number; lat: number }) => Number.isFinite(label.lon) && Number.isFinite(label.lat));
      renderLabels(true);
    } catch (error) {
      console.warn("District labels unavailable", error);
    }
    void map;
  }

  function scheduleLabels(delay = 150) {
    if (labelTimer.current) window.clearTimeout(labelTimer.current);
    labelTimer.current = window.setTimeout(() => {
      labelTimer.current = null;
      renderLabels();
    }, delay);
  }

  function renderLabels(force = false) {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const zoom = map.getZoom();
    const bounds = map.getBounds();
    const signature = `${zoom.toFixed(1)}:${bounds.getWest().toFixed(3)}:${bounds.getSouth().toFixed(3)}:${bounds.getEast().toFixed(3)}:${bounds.getNorth().toFixed(3)}`;
    if (!force && signature === labelSignature.current) return;
    labelSignature.current = signature;
    try {
      labelMarkers.current.forEach((marker) => marker.remove());
      labelMarkers.current = [];
      const candidates: Array<{ name: string; lat: number; lon: number; kind: string; weight: number }> = [];
    for (const label of adminLabels.current) {
      if (!bounds.contains([label.lon, label.lat])) continue;
      if (zoom >= 7.5) candidates.push({ ...label, kind: "district", weight: 2 });
    }
    const priority: Record<string, number> = { city: 0, town: 1, village: 2, hamlet: 3, suburb: 4, neighbourhood: 4 };
    const visiblePlaces = placesRef.current
      .filter((place) => bounds.contains([place.lon, place.lat]))
      .filter((place) => (zoom >= 9.5 ? true : place.type === "city" || place.type === "town"))
      .sort((a, b) => (priority[a.type] ?? 9) - (priority[b.type] ?? 9))
      .slice(0, zoom >= 11 ? 22 : 14);
    for (const place of visiblePlaces) {
      candidates.push({ ...place, kind: place.type, weight: 1 });
    }
    const cap = zoom >= 11 ? 34 : zoom >= 9 ? 26 : 16;
    for (const candidate of candidates.slice(0, cap)) {
      const element = document.createElement("div");
      element.className = `map-label map-label--${candidate.kind}`;
      element.textContent = candidate.name;
      const marker = new maplibregl.Marker({ element, anchor: "center" })
        .setLngLat([candidate.lon, candidate.lat])
        .addTo(map);
      labelMarkers.current.push(marker);
    }
    } catch (error) {
      console.warn("Label rendering failed", error);
    }
  }

  // -------------------------------------------------------------- interactions
  function handleMapClick(lat: number, lon: number) {
    const currentMode = modeRef.current;
    if (currentMode === "draw" || currentMode === "gps") {
      if (currentMode === "gps") return;
      addVertex(lon, lat);
      return;
    }
    if (currentMode === "split") {
      splitPoints.current.push([lon, lat]);
      updateSplitSource();
      if (splitPoints.current.length === 2) {
        const [start, end] = splitPoints.current;
        splitPoints.current = [];
        updateSplitSource();
        callbacksRef.current.onSplit({
          type: "LineString",
          coordinates: [start, end],
        } as unknown as Geometry);
      }
      return;
    }
    if (currentMode === "edit") return;
    callbacksRef.current.onMapClick(lat, lon);
  }

  function addVertex(lon: number, lat: number) {
    const map = mapRef.current;
    const draft = draftRef.current;
    if (map && draft.length >= 3) {
      const first = map.project([draft[0][0], draft[0][1]]);
      const current = map.project([lon, lat]);
      if (Math.hypot(first.x - current.x, first.y - current.y) < 16) {
        finishDraw();
        return;
      }
    }
    // Snap to an existing farm vertex when close, unless it would be a duplicate.
    if (map) {
      const current = map.project([lon, lat]);
      for (const saved of farms) {
        const ring =
          saved.geometry.type === "Polygon"
            ? (saved.geometry.coordinates as number[][][])[0]
            : (saved.geometry.coordinates as number[][][][])[0][0];
        for (const point of ring) {
          const projected = map.project([point[0], point[1]]);
          if (Math.hypot(projected.x - current.x, projected.y - current.y) < 12) {
            lon = point[0];
            lat = point[1];
            break;
          }
        }
      }
    }
    draft.push([lon, lat]);
    redoRef.current = [];
    syncDraft();
  }

  function syncDraft() {
    const map = mapRef.current;
    if (!map) return;
    const draft = draftRef.current;
    const lineSource = map.getSource("draft") as maplibregl.GeoJSONSource | undefined;
    const vertexSource = map.getSource("vertices") as maplibregl.GeoJSONSource | undefined;
    if (!lineSource || !vertexSource) return;
    const features: GeoJSON.Feature[] = [];
    if (draft.length >= 2) {
      features.push({
        type: "Feature",
        properties: {},
        geometry:
          draft.length >= 3
            ? (polygonGeometry(draft) as unknown as GeoJSON.Polygon)
            : ({ type: "LineString", coordinates: draft } as GeoJSON.LineString),
      });
    }
    lineSource.setData({ type: "FeatureCollection", features });
    vertexSource.setData({
      type: "FeatureCollection",
      features: draft.map((point) => ({
        type: "Feature",
        properties: {},
        geometry: { type: "Point", coordinates: point },
      })),
    });
    renderVertexMarkers();
    if (draft.length >= 3) {
      const ring = polygonGeometry(draft).coordinates as number[][][];
      callbacksRef.current.onDraftChange(polygonGeometry(draft), {
        areaHa: ringAreaHa(ring[0]),
        perimeterM: ringPerimeterM(ring[0]),
        vertices: draft.length,
      });
    } else {
      callbacksRef.current.onDraftChange(null, {
        areaHa: 0,
        perimeterM: draft.length >= 2 ? ringPerimeterM(draft) : 0,
        vertices: draft.length,
      });
    }
  }

  function renderVertexMarkers() {
    const map = mapRef.current;
    if (!map) return;
    vertexMarkers.current.forEach((marker) => marker.remove());
    vertexMarkers.current = [];
    const editable = modeRef.current === "edit" || modeRef.current === "draw";
    if (!editable) return;
    draftRef.current.forEach((point, index) => {
      const element = document.createElement("button");
      element.type = "button";
      element.className = "vertex-marker";
      element.setAttribute("aria-label", `Vertex ${index + 1} — drag to move`);
      element.dataset.index = String(index);
      const marker = new maplibregl.Marker({ element, draggable: true })
        .setLngLat([point[0], point[1]])
        .addTo(map);
      marker.on("dragend", () => {
        const position = marker.getLngLat();
        draftRef.current[index] = [position.lng, position.lat];
        syncDraft();
      });
      vertexMarkers.current.push(marker);
    });
  }

  function finishDraw() {
    const draft = draftRef.current;
    if (draft.length < 3) return;
    const geometry = polygonGeometry(draft);
    callbacksRef.current.onDraftComplete(geometry);
    clearDrawing(false);
  }

  function clearDrawing(notify = true) {
    draftRef.current = [];
    redoRef.current = [];
    vertexMarkers.current.forEach((marker) => marker.remove());
    vertexMarkers.current = [];
    const map = mapRef.current;
    if (map) {
      (map.getSource("draft") as maplibregl.GeoJSONSource | undefined)?.setData(emptyCollection());
      (map.getSource("vertices") as maplibregl.GeoJSONSource | undefined)?.setData(emptyCollection());
    }
    if (notify) callbacksRef.current.onDraftChange(null, null);
  }

  function updateSplitSource() {
    const map = mapRef.current;
    if (!map) return;
    const source = map.getSource("split") as maplibregl.GeoJSONSource | undefined;
    if (!source) return;
    const points = splitPoints.current;
    if (points.length < 2) {
      source.setData({
        type: "FeatureCollection",
        features: points.map((point) => ({
          type: "Feature",
          properties: {},
          geometry: { type: "Point", coordinates: point },
        })),
      });
      return;
    }
    source.setData({
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: points } }],
    });
  }

  function stopGps() {
    if (gpsWatch.current !== null) {
      navigator.geolocation.clearWatch(gpsWatch.current);
      gpsWatch.current = null;
    }
  }

  function startGps() {
    if (!navigator.geolocation) {
      callbacksRef.current.onGpsStatus({ active: false, points: 0, accuracy: null, error: "Geolocation is not supported" });
      return;
    }
    gpsPoints.current = [];
    const map = mapRef.current;
    callbacksRef.current.onGpsStatus({ active: true, points: 0, accuracy: null });
    gpsWatch.current = navigator.geolocation.watchPosition(
      (position) => {
        const { latitude, longitude, accuracy } = position.coords;
        const points = gpsPoints.current;
        const last = points[points.length - 1];
        const distance =
          last && map
            ? Math.hypot(
                map.project([last[0], last[1]]).x - map.project([longitude, latitude]).x,
                map.project([last[0], last[1]]).y - map.project([longitude, latitude]).y,
              )
            : Infinity;
        if (!last || distance > 8) {
          points.push([longitude, latitude]);
          const source = map?.getSource("gps") as maplibregl.GeoJSONSource | undefined;
          source?.setData({
            type: "FeatureCollection",
            features: points.map((point) => ({
              type: "Feature",
              properties: {},
              geometry: { type: "Point", coordinates: point },
            })),
          });
          map?.easeTo({ center: [longitude, latitude], zoom: Math.max(map.getZoom(), 16) });
          callbacksRef.current.onGpsStatus({ active: true, points: points.length, accuracy });
        } else {
          callbacksRef.current.onGpsStatus({ active: true, points: points.length, accuracy });
        }
      },
      (error) => {
        stopGps();
        callbacksRef.current.onGpsStatus({
          active: false,
          points: gpsPoints.current.length,
          accuracy: null,
          error: error.message || "Location permission denied",
        });
      },
      { enableHighAccuracy: true, maximumAge: 4000, timeout: 20000 },
    );
  }

  // --------------------------------------------------------------------- effects
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (mode === "draw") {
      map.doubleClickZoom.disable();
      map.getCanvas().style.cursor = "crosshair";
      clearDrawing();
    } else if (mode === "edit") {
      map.doubleClickZoom.disable();
      map.getCanvas().style.cursor = "grab";
      renderVertexMarkers();
    } else if (mode === "split") {
      map.getCanvas().style.cursor = "cell";
      splitPoints.current = [];
      updateSplitSource();
    } else if (mode === "gps") {
      map.getCanvas().style.cursor = "crosshair";
      startGps();
    } else {
      map.doubleClickZoom.enable();
      map.getCanvas().style.cursor = "";
      stopGps();
      if (mode === "idle") {
        renderVertexMarkers();
      }
    }
    if (mode !== "gps") {
      callbacksRef.current.onGpsStatus({ active: false, points: 0, accuracy: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const data = {
      type: "FeatureCollection" as const,
      features: farms.map((item) => ({
        type: "Feature" as const,
        properties: { id: item.id, name: item.name, selected: item.id === selectedFarmId },
        geometry: item.geometry as unknown as GeoJSON.Geometry,
      })),
    };
    (map.getSource("farms") as maplibregl.GeoJSONSource | undefined)?.setData(data);
    const selectedFeature = farm
      ? [{ type: "Feature" as const, properties: { id: farm.id }, geometry: farm.geometry as unknown as GeoJSON.Geometry }]
      : [];
    (map.getSource("selected") as maplibregl.GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: selectedFeature,
    });
  }, [farms, selectedFarmId, farm, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const wanted = new Map<string, MapOverlay>();
    for (const overlay of overlays) {
      if (!overlay.url || overlay.corners?.length !== 4) continue;
      wanted.set(`overlay-${overlay.id}`, overlay);
    }

    // Remove overlays that are no longer supplied (for example after a farm change).
    for (const id of Array.from(overlayIds.current)) {
      if (wanted.has(id)) continue;
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
      overlayIds.current.delete(id);
      delete overlayUrls.current[id];
    }

    for (const [id, overlay] of wanted) {
      const coordinates = overlay.corners as [
        [number, number],
        [number, number],
        [number, number],
        [number, number],
      ];
      const urlChanged = overlayUrls.current[id] !== overlay.url;
      if (urlChanged) {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
        map.addSource(id, { type: "image", url: overlay.url, coordinates });
        const before = map.getLayer("farms-fill") ? "farms-fill" : undefined;
        map.addLayer(
          {
            id,
            type: "raster",
            source: id,
            paint: {
              "raster-opacity": overlay.opacity,
              "raster-fade-duration": 120,
              "raster-resampling": "nearest",
            },
          },
          before,
        );
        overlayIds.current.add(id);
        overlayUrls.current[id] = overlay.url;
      }
      map.setLayoutProperty(id, "visibility", overlay.visible ? "visible" : "none");
      map.setPaintProperty(id, "raster-opacity", overlay.opacity);
    }
  }, [overlays, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const id = "satellite-tiles";
    if (!map.getSource(id)) {
      // Insert below any evidence overlays and below the farm boundaries, so the
      // basemap never covers the data layers.
      const before = map
        .getStyle()
        .layers.find((layer) => layer.id.startsWith("overlay-") || layer.id === "farms-fill")?.id;
      map.addSource(id, {
        type: "raster",
        tiles: [SATELLITE_TILES],
        tileSize: 256,
        maxzoom: 14,
        attribution: SATELLITE_ATTRIBUTION,
      });
      map.addLayer(
        {
          id,
          type: "raster",
          source: id,
          paint: { "raster-opacity": 1, "raster-fade-duration": 200 },
        },
        before,
      );
    }
    map.setLayoutProperty(id, "visibility", satellite ? "visible" : "none");
  }, [satellite, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    applyLayerState(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers, satellite, mapReady]);

  function applyLayerState(map: MLMap) {
    const mapping: Record<string, string[]> = {
      districts: ["district-line"],
      roads: ["roads-casing-major", "roads-main-major", "roads-casing-minor", "roads-main-minor"],
      water: ["water-fill", "water-line"],
      farms: ["farms-fill", "farms-line"],
    };
    const hiddenForSatellite = new Set(["basemap", "districts", "roads", "water"]);
    for (const id of ["region-fill", "region-line"]) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, "visibility", satellite ? "none" : "visible");
      }
    }
    for (const layer of layers) {
      const ids = mapping[layer.id] ?? [];
      const visible = layer.visible && !(satellite && hiddenForSatellite.has(layer.id));
      for (const id of ids) {
        if (!map.getLayer(id)) continue;
        map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
      }
    }
  }

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const dark = theme === "dark";
    const set = (layer: string, property: string, value: unknown) => {
      if (map.getLayer(layer)) map.setPaintProperty(layer, property, value as never);
    };
    set("background", "background-color", dark ? "#0b110e" : "#f4f1e6");
    set("region-fill", "fill-color", dark ? "#131c17" : "#eeeade");
    set("region-line", "line-color", dark ? "#28382f" : "#c9c3ae");
    set("district-line", "line-color", dark ? "#1d2b24" : "#dcd7c6");
    set("water-fill", "fill-color", dark ? "#0e2434" : "#b9d6e8");
    set("water-line", "line-color", dark ? "#17374d" : "#8ebbd6");
    set("roads-casing-major", "line-color", dark ? "#0a0f0c" : "#d9d3c0");
    set("roads-casing-minor", "line-color", dark ? "#0a0f0c" : "#e2ddcc");
    set("roads-main-major", "line-color", dark ? "#3c4d42" : "#ffffff");
    set("roads-main-minor", "line-color", dark ? "#2b3a31" : "#f6f3e8");
    if (map.getLayer("places-circle")) {
      map.setPaintProperty("places-circle", "circle-color", (dark ? "#c8d3c9" : "#4a5449") as never);
    }
    document.documentElement.dataset.theme = theme;
  }, [theme, mapReady]);

  function fitGeometry(geometry: Geometry, paddingBottom?: number) {
    const map = mapRef.current;
    if (!map) return;
    const mobile = window.innerWidth <= 980;
    const [sw, ne] = bboxOfGeometry(geometry);
    map.fitBounds([sw, ne], {
      padding: {
        top: 92,
        bottom: paddingBottom ?? (mobile ? Math.round(window.innerHeight * 0.6) : 150),
        left: mobile ? 22 : 34,
        right: mobile ? 22 : 34,
      },
      maxZoom: 17,
      duration: 700,
    });
  }

  useEffect(() => {
    if (!farm) {
      fittedFarmId.current = null;
      return;
    }
    if (!mapReady) return;
    if (fittedFarmId.current === farm.id) return;
    fittedFarmId.current = farm.id;
    const timer = window.setTimeout(() => fitGeometry(farm.geometry), 120);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [farm?.id, mapReady]);

  useImperativeHandle(
    ref,
    (): MapApi => ({
      finishDraw,
      finishGps: () => {
        stopGps();
        const points = gpsPoints.current;
        const map = mapRef.current;
        if (map) (map.getSource("gps") as maplibregl.GeoJSONSource | undefined)?.setData(emptyCollection());
        if (points.length < 3) return null;
        gpsPoints.current = [];
        return polygonGeometry(points);
      },
      undo: () => {
        if (!draftRef.current.length) return;
        redoRef.current.push(draftRef.current.pop() as number[]);
        syncDraft();
      },
      redo: () => {
        const point = redoRef.current.pop();
        if (!point) return;
        draftRef.current.push(point);
        syncDraft();
      },
      cancel: () => clearDrawing(),
      loadGeometry: (geometry: Geometry) => {
        const ring =
          geometry.type === "Polygon"
            ? (geometry.coordinates as number[][][])[0]
            : (geometry.coordinates as number[][][][])[0][0];
        draftRef.current = ring.slice(0, -1).map((point) => [point[0], point[1]]);
        redoRef.current = [];
        syncDraft();
      },
      getDraft: () => (draftRef.current.length >= 3 ? polygonGeometry(draftRef.current) : null),
      fitFarm: (geometry: Geometry, options?: { paddingBottom?: number }) => {
        fitGeometry(geometry, options?.paddingBottom);
      },
      fitSatellite: (corners: number[][]) => {
        const map = mapRef.current;
        if (!map || corners.length !== 4) return;
        const lons = corners.map((corner) => corner[0]);
        const lats = corners.map((corner) => corner[1]);
        // Fit an inset so the image bleeds past the canvas edges instead of
        // floating as a square with empty margins.
        const centreLon = (Math.min(...lons) + Math.max(...lons)) / 2;
        const centreLat = (Math.min(...lats) + Math.max(...lats)) / 2;
        const halfLon = ((Math.max(...lons) - Math.min(...lons)) / 2) * 0.8;
        const halfLat = ((Math.max(...lats) - Math.min(...lats)) / 2) * 0.8;
        const mobile = window.innerWidth <= 980;
        map.fitBounds(
          [
            [centreLon - halfLon, centreLat - halfLat],
            [centreLon + halfLon, centreLat + halfLat],
          ],
          {
            padding: mobile
              ? { top: 8, bottom: Math.round(window.innerHeight * 0.45), left: 8, right: 8 }
              : { top: 0, bottom: 0, left: 0, right: 0 },
            maxZoom: 16,
            duration: 700,
          },
        );
      },
      flyTo: (lat: number, lon: number, zoom = 13) => {
        mapRef.current?.flyTo({ center: [lon, lat], zoom, duration: 900 });
      },
      setTerrain: (enabled: boolean) => {
        const map = mapRef.current;
        if (!map) return;
        if (enabled) {
          if (!map.getSource("terrain-dem")) {
            map.addSource("terrain-dem", {
              type: "raster-dem",
              tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
              encoding: "terrarium",
              tileSize: 256,
              maxzoom: 12,
              attribution: "Terrain: Mapzen/AWS elevation tiles",
            });
          }
          map.setTerrain({ source: "terrain-dem", exaggeration: 1.3 });
        } else {
          map.setTerrain(null);
        }
      },
      resetSplit: () => {
        splitPoints.current = [];
        updateSplitSource();
      },
      map: () => mapRef.current,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return (
    <div className="map-shell">
      <div ref={containerRef} className="map-canvas" aria-label="Map of Ghana and selected farm boundaries" />
      {!mapReady && (
        <div className="map-loading" role="status">
          <span className="spinner" aria-hidden="true" />
          Loading Ghana base map…
        </div>
      )}
    </div>
  );
});
