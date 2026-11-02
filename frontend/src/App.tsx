import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, downloadExport, getAccessToken, pollJob, relativeAge, setAccessToken } from "./api";
import type {
  AccountUser,
  Analysis,
  CropSummary,
  Farm,
  Geometry,
  Job,
  LayerChip,
  LayerState,
  MapOverlay,
  ProviderSpec,
} from "./types";
import { MapView, type DraftStats, type MapApi, type MapMode } from "./map/MapView";
import {
  FarmPanel,
  LayersPanel,
  Onboarding,
  SaveFarmModal,
  SearchOverlay,
  SettingsModal,
  Timeline,
  TopBar,
  type TabId,
} from "./components/panels";
import { Toast } from "./components/ui";
import { analysisScopeKey, offline } from "./offline";
import type { LanguageCode } from "./i18n";

type Theme = "dark" | "daylight";

const EVIDENCE_LAYER_IDS = new Set(["soil-ph", "soil-carbon", "soil-texture", "cropland", "slope", "elevation"]);

const DEFAULT_LAYERS: LayerState[] = [
  {
    id: "basemap",
    name: "Local vector basemap",
    visible: true,
    opacity: 1,
    kind: "vector",
    available: true,
    description: "OpenStreetMap roads, water and place labels served by this app. No public tile server is used.",
  },
  {
    id: "districts",
    name: "District boundaries",
    visible: true,
    opacity: 0.85,
    kind: "vector",
    available: true,
    description: "geoBoundaries ADM2 â€” 260 districts.",
  },
  {
    id: "roads",
    name: "Roads",
    visible: true,
    opacity: 0.9,
    kind: "vector",
    available: true,
    description: "Major roads from the Geofabrik Ghana extract.",
  },
  {
    id: "water",
    name: "Water bodies and rivers",
    visible: true,
    opacity: 0.9,
    kind: "vector",
    available: true,
    description: "Lakes, reservoirs and major rivers.",
  },
  {
    id: "farms",
    name: "Saved farm boundaries",
    visible: true,
    opacity: 1,
    kind: "vector",
    available: true,
    description: "Your private boundaries. Dashed outline for all farms, amber for the selected farm.",
  },
  {
    id: "true-color",
    name: "Sentinel-2 true colour",
    visible: false,
    opacity: 0.85,
    kind: "imagery",
    available: false,
    unavailableReason: "Run an analysis to load the latest cloud-free scene.",
    description: "Latest usable scene, 10 m, clipped to this field.",
  },
  {
    id: "ndvi",
    name: "NDVI",
    visible: false,
    opacity: 0.8,
    kind: "imagery",
    available: false,
    unavailableReason: "Run an analysis to compute NDVI for this polygon.",
    description: "Vegetation index from cloud-masked red and near-infrared bands.",
    legend: [
      { color: "#8c510a", label: "Low" },
      { color: "#dfc27d", label: "Moderate" },
      { color: "#01665e", label: "High" },
    ],
  },
  {
    id: "ndmi",
    name: "NDMI",
    visible: false,
    opacity: 0.8,
    kind: "imagery",
    available: false,
    unavailableReason: "Run an analysis to compute NDMI for this polygon.",
    description: "Canopy moisture index from NIR and short-wave infrared.",
    legend: [
      { color: "#8c510a", label: "Dry" },
      { color: "#dfc27d", label: "Moderate" },
      { color: "#01665e", label: "Wet" },
    ],
  },
  {
    id: "soil-ph",
    name: "Soil pH (250 m)",
    visible: false,
    opacity: 0.75,
    kind: "derived",
    available: true,
    description: "Toggle to render this field's 250 m SoilGrids cells. Each cell is 250 m â€” blocky by design.",
  },
  {
    id: "soil-carbon",
    name: "Soil organic carbon (250 m)",
    visible: false,
    opacity: 0.75,
    kind: "derived",
    available: true,
    description: "Toggle to render this field's 250 m SoilGrids cells (0â€“5 cm).",
  },
  {
    id: "soil-texture",
    name: "Clay content (250 m)",
    visible: false,
    opacity: 0.75,
    kind: "derived",
    available: true,
    description: "Clay fraction of the field's 250 m cells; the texture class is in the Soil tab.",
  },
  {
    id: "cropland",
    name: "Land cover (2021 baseline)",
    visible: false,
    opacity: 0.7,
    kind: "derived",
    available: true,
    description: "Toggle to render ESA WorldCover 2021 classes for this field (10 m). A historical baseline.",
  },
  {
    id: "slope",
    name: "Slope (30 m)",
    visible: false,
    opacity: 0.7,
    kind: "derived",
    available: true,
    description: "Toggle to render slope derived from Copernicus DEM GLO-30 for this field.",
  },
  {
    id: "elevation",
    name: "Elevation (30 m)",
    visible: false,
    opacity: 0.7,
    kind: "derived",
    available: true,
    description: "Toggle to render Copernicus DEM GLO-30 elevation for this field.",
  },
  {
    id: "evapotranspiration",
    name: "Evapotranspiration (WaPOR)",
    visible: false,
    opacity: 0.7,
    kind: "derived",
    available: false,
    unavailableReason: "FAO WaPOR API returned HTTP 502 at verification; no values are invented.",
    description: "WaPOR AETI and net primary production.",
  },
];

function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function store(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage may be full or blocked */
  }
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => readStored<Theme>("gfi.theme", "dark"));
  const [language, setLanguage] = useState<LanguageCode>(() => readStored<LanguageCode>("gfi.language", "en"));
  const [units, setUnits] = useState<"metric" | "imperial">(() => readStored<"metric" | "imperial">("gfi.units", "metric"));

  const [farms, setFarms] = useState<Farm[]>([]);
  const [farmsSavedAt, setFarmsSavedAt] = useState<string | null>(null);
  const [selectedFarmId, setSelectedFarmId] = useState<string | null>(null);
  const [farm, setFarm] = useState<Farm | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysisSavedAt, setAnalysisSavedAt] = useState<string | null>(null);
  const [activeFieldId, setActiveFieldId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [partial, setPartial] = useState<Analysis["sections"] | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const [layers, setLayers] = useState<LayerState[]>(DEFAULT_LAYERS);
  const [layerChips, setLayerChips] = useState<Record<string, LayerChip>>({});
  const layerRequests = useRef<Set<string>>(new Set());
  const [mode, setMode] = useState<MapMode>("idle");
  const [draft, setDraft] = useState<{ geometry: Geometry | null; stats: DraftStats | null }>({ geometry: null, stats: null });
  const [pendingGeometry, setPendingGeometry] = useState<Geometry | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveOverlaps, setSaveOverlaps] = useState<Array<{ name: string; overlap_ha: number }>>([]);
  const [saveLocation, setSaveLocation] = useState<{ region: string | null; district: string | null }>({ region: null, district: null });
  const [gps, setGps] = useState<{ active: boolean; points: number; accuracy: number | null; error?: string }>({
    active: false,
    points: 0,
    accuracy: null,
  });
  const [crops, setCrops] = useState<CropSummary[]>([]);
  const [providers, setProviders] = useState<Array<Record<string, unknown>>>([]);
  const [providersLoading, setProvidersLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [layersOpen, setLayersOpen] = useState(() => window.innerWidth > 980);
  const [panelOpen, setPanelOpen] = useState(() => window.innerWidth > 980);
  const [terrain, setTerrain] = useState(false);
  const [satelliteOn, setSatelliteOn] = useState(false);
  const [timelineRange, setTimelineRange] = useState<30 | 90 | 180>(30);
  const [toast, setToast] = useState<{ message: string; tone: "info" | "success" | "error" } | null>(null);
  const [online, setOnline] = useState(navigator.onLine);
  const [booting, setBooting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [cropsBusy, setCropsBusy] = useState(false);
  const [usage, setUsage] = useState<{ usedMB: number; quotaMB: number | null } | null>(null);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const [mapNotice, setMapNotice] = useState<string | null>(null);
  const [gateOpen, setGateOpen] = useState(false);
  const [gateToken, setGateToken] = useState("");
  const [accountsEnabled, setAccountsEnabled] = useState(false);
  const [authUser, setAuthUser] = useState<AccountUser | null>(null);
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  const mapRef = useRef<MapApi | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const lastAnalysisKey = useRef<string | null>(null);
  const analysisAbort = useRef<AbortController | null>(null);
  const toastTimer = useRef<number | null>(null);
  const runAnalysisRef = useRef<((options?: { force?: boolean; cropId?: string | null; farmOverride?: Farm }) => Promise<void>) | null>(null);
  // Monotonic tokens: a late response for a previous farm, field or run must
  // never be written into the state of whatever is selected now.
  const selectionToken = useRef(0);
  const analysisRun = useRef(0);
  const layerEpoch = useRef(0);

  const notify = useCallback((message: string, tone: "info" | "success" | "error" = "info") => {
    setToast({ message, tone });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 5200);
  }, []);

  /* ------------------------------------------------------------- bootstrapping */
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    store("gfi.theme", theme);
  }, [theme]);
  useEffect(() => store("gfi.language", language), [language]);
  useEffect(() => store("gfi.units", units), [units]);

  useEffect(() => {
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  // A deployment can require one shared access token. Ask for it up front when
  // the server says so, and whenever any request comes back 401. Accounts take
  // precedence: when they exist, signing in is what the 401 means.
  useEffect(() => {
    const onUnauthorized = () => {
      void api
        .authMe()
        .then((payload) => {
          if (payload.accounts_enabled) setAuthUser(null);
          else setGateOpen(true);
        })
        .catch(() => setGateOpen(true));
    };
    window.addEventListener("gfi:unauthorized", onUnauthorized);
    let cancelled = false;
    api
      .authMe()
      .then((payload) => {
        if (cancelled) return;
        setAccountsEnabled(payload.accounts_enabled);
        setAuthUser(payload.user);
        if (payload.posture === "token" && !getAccessToken()) setGateOpen(true);
      })
      .catch(() => {
        /* offline: the normal offline path applies */
      });
    return () => {
      cancelled = true;
      window.removeEventListener("gfi:unauthorized", onUnauthorized);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await api.listFarms();
        if (cancelled) return;
        setFarms(payload.farms);
        setFarmsSavedAt(new Date().toISOString());
        void offline.saveFarms(payload.farms);
      } catch {
        const cached = await offline.loadFarms();
        if (cancelled) return;
        setFarms(cached.farms);
        setFarmsSavedAt(cached.savedAt);
        setOnline(false);
      } finally {
        if (!cancelled) setBooting(false);
      }
      try {
        const catalog = await api.crops();
        if (!cancelled) setCrops(catalog.crops);
      } catch {
        /* crop catalogue also cached by the service worker */
      }
      try {
        const providerPayload = await api.providers();
        if (!cancelled) setProviders(providerPayload.providers as unknown as Array<Record<string, unknown>>);
      } catch {
        /* provider list is not essential for the map */
      }
      const estimate = await offline.usage().catch(() => null);
      if (!cancelled) setUsage(estimate);
    })();
    return () => {
      cancelled = true;
      analysisAbort.current?.abort();
    };
  }, []);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        setSearchOpen(true);
      }
      if (event.key === "Escape") {
        if (mode !== "idle" && !saveOpen) {
          setMode("idle");
          mapRef.current?.cancel();
          setDraft({ geometry: null, stats: null });
        }
      }
      if (!typing && !event.metaKey && !event.ctrlKey) {
        if (event.key === "d") beginDraw();
        if (event.key === "e" && farm) beginEdit();
        if (event.key === "g" && farm) startGpsWalk();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, farm, saveOpen]);

  /* ---------------------------------------------------------------- selection */
  const refreshFarms = useCallback(async () => {
    try {
      const payload = await api.listFarms();
      setFarms(payload.farms);
      setFarmsSavedAt(new Date().toISOString());
      void offline.saveFarms(payload.farms);
    } catch {
      const cached = await offline.loadFarms();
      setFarms(cached.farms);
      setFarmsSavedAt(cached.savedAt);
    }
  }, []);

  const selectFarm = useCallback(
    async (farmId: string) => {
      const token = ++selectionToken.current;
      setSelectedFarmId(farmId);
      setMode("idle");
      setActiveFieldId(null);
      setAnalysis(null);
      setPartial(null);
      setJob(null);
      lastAnalysisKey.current = null;
      setPanelOpen(true);
      setLayersOpen(window.innerWidth > 980);
      try {
        const payload = await api.getFarm(farmId);
        if (selectionToken.current !== token) return; // a newer selection won
        setFarm(payload.farm);
        setOnline(true);
        void runAnalysisRef.current?.({ farmOverride: payload.farm });
      } catch {
        if (selectionToken.current !== token) return;
        const fallback = farms.find((item) => item.id === farmId) ?? null;
        setFarm(fallback);
        if (fallback) mapRef.current?.fitFarm(fallback.geometry);
        setOnline(false);
      }
    },
    [farms],
  );

  useEffect(() => {
    if (booting || selectedFarmId || farms.length === 0) return;
    // Returning users should land on their most recent farm and its analysis.
    void selectFarm(farms[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting, farms, selectedFarmId]);

  useEffect(() => {
    if (!selectedFarmId) return;
    let cancelled = false;
    const farmScope = analysisScopeKey(selectedFarmId, null);
    (async () => {
      try {
        const saved = await offline.loadAnalysis(farmScope);
        // Only a whole-farm result may stand in for the farm: a field's analysis
        // describes a smaller boundary and would misstate every area metric.
        if (!cancelled && saved && (saved.analysis.farm?.field_id ?? null) === null) {
          setAnalysis((current) => current ?? saved.analysis);
          setAnalysisSavedAt(saved.savedAt);
        }
      } catch {
        /* ignore cache errors */
      }
      try {
        const latest = await api.latestAnalysis(selectedFarmId);
        if (!cancelled && latest.analysis) {
          setAnalysis((current) => current ?? latest.analysis);
          setAnalysisSavedAt(latest.analysis.generated_at);
          void offline.saveAnalysis(
            analysisScopeKey(selectedFarmId, latest.analysis.farm?.field_id ?? null),
            latest.analysis,
          );
        }
      } catch {
        /* offline: cached analysis is used */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedFarmId]);

  /* ---------------------------------------------------------------- analysis */
  const runAnalysis = useCallback(
    async (options: { force?: boolean; cropId?: string | null; farmOverride?: Farm; fieldId?: string | null } = {}) => {
      const target = options.farmOverride ?? farm;
      if (!target) return;
      const fieldId = options.fieldId ?? activeFieldId ?? null;
      const scope = analysisScopeKey(target.id, fieldId);
      const runId = ++analysisRun.current;
      const superseded = () => analysisRun.current !== runId;
      analysisAbort.current?.abort();
      const controller = new AbortController();
      analysisAbort.current = controller;
      lastAnalysisKey.current = `${target.id}:${target.crop_id}:${target.planting_date}:${target.updated_at}`;
      setBusy(true);
      setJob(null);
      setPartial(null);
      setAnalysis(null);
      setAnalysisSavedAt(null);
      setTab((current) => (current === "crops" ? "crops" : "overview"));
      try {
        const response = await api.startAnalysis(
          target.id,
          {
            crop_id: options.cropId ?? target.crop_id ?? null,
            planting_date: target.planting_date ?? null,
            management_level: "medium",
            field_id: fieldId,
            force: options.force ?? false,
          },
          controller.signal,
        );
        if (superseded()) return;
        if (response.cached && response.analysis) {
          setAnalysis(response.analysis);
          setAnalysisSavedAt(response.analysis.generated_at);
          void offline.saveAnalysis(scope, response.analysis);
          notify("Loaded a recent analysis for this boundary.", "info");
          return;
        }
        if (!response.job_id) return;
        const finished = await pollJob(
          response.job_id,
          (update) => {
            if (superseded()) return;
            setJob(update);
            if (update.result && "partial" in (update.result as object)) {
              setPartial((update.result as { sections: Analysis["sections"] }).sections);
            }
          },
          { signal: controller.signal },
        );
        if (superseded()) return;
        if (finished.status === "complete" && finished.result && !("partial" in (finished.result as object))) {
          const payload = finished.result as Analysis;
          setAnalysis(payload);
          setAnalysisSavedAt(payload.generated_at);
          void offline.saveAnalysis(scope, payload);
          notify("Analysis complete â€” every value shows its source and date.", "success");
        } else if (finished.status === "failed") {
          notify(finished.error ?? finished.message ?? "Analysis failed", "error");
        }
      } catch (error) {
        if (superseded() || (error as ApiError).status === 499) return;
        notify(error instanceof Error ? error.message : "Analysis failed", "error");
        setOnline(false);
      } finally {
        // Only the run that is still current may clear the progress state; an
        // aborted run's cleanup used to wipe the new run's spinner.
        if (!superseded()) {
          setBusy(false);
          setJob(null);
        }
      }
    },
    [farm, notify, activeFieldId],
  );
  runAnalysisRef.current = runAnalysis;

  useEffect(() => {
    const latest = analysis?.sections.vegetation?.data?.latest;
    setLayers((current) =>
      current.map((layer) => {
        if (layer.id === "true-color") {
          return { ...layer, available: Boolean(latest?.chips?.true_color), visible: Boolean(latest?.chips?.true_color) ? layer.visible : false };
        }
        if (layer.id === "ndvi") {
          return { ...layer, available: Boolean(latest?.chips?.ndvi), visible: Boolean(latest?.chips?.ndvi) ? true : false };
        }
        if (layer.id === "ndmi") {
          return { ...layer, available: Boolean(latest?.chips?.ndmi), visible: Boolean(latest?.chips?.ndmi) ? layer.visible : false };
        }
        return layer;
      }),
    );
  }, [analysis]);

  /* ------------------------------------------------------------------ drawing */
  const beginDraw = useCallback(() => {
    setMode("draw");
    setDraft({ geometry: null, stats: null });
    setPanelOpen(false);
    setLayersOpen(false);
    setMapNotice("Tap each corner. Tap the first point again, or press Finish, to close the boundary.");
  }, []);

  const beginEdit = useCallback(() => {
    if (!farm) return;
    if (farm.geometry.type !== "Polygon") {
      // loadGeometry keeps only the first ring, so editing a multi-part boundary
      // would silently delete every other part on save.
      notify(
        "This boundary has several parts. Editing works on single-part boundaries â€” redraw it, or split it into fields instead.",
        "error",
      );
      return;
    }
    setMode("edit");
    mapRef.current?.loadGeometry(farm.geometry);
    setPanelOpen(false);
    setMapNotice("Drag any vertex to move it, then save the changes.");
  }, [farm, notify]);

  const beginSplit = useCallback(() => {
    if (!farm) return;
    setMode("split");
    mapRef.current?.resetSplit();
    setMapNotice("Tap two points to draw a line across the field. The farm is split into fields along it.");
  }, [farm]);

  const startGpsWalk = useCallback(() => {
    setMode("gps");
    setGps({ active: true, points: 0, accuracy: null });
    setPanelOpen(false);
    setMapNotice("Walk the boundary. Points are recorded when you move more than 8 m.");
  }, []);

  const endMode = useCallback(() => {
    setMode("idle");
    setDraft({ geometry: null, stats: null });
    setMapNotice(null);
    mapRef.current?.cancel();
  }, []);

  const handleDraftChange = useCallback((geometry: Geometry | null, stats: DraftStats | null) => {
    setDraft({ geometry, stats });
  }, []);

  const handleDraftComplete = useCallback(
    async (geometry: Geometry) => {
      setMode("idle");
      setMapNotice(null);
      const ring =
        geometry.type === "Polygon"
          ? (geometry.coordinates as number[][][])[0]
          : (geometry.coordinates as number[][][][])[0][0];
      const vertexCount = Math.max(0, ring.length - 1);
      try {
        const validation = await api.validateGeometry(geometry);
        if (!validation.valid) {
          notify(validation.error?.message ?? "This boundary is not valid.", "error");
          setPendingGeometry(null);
          return;
        }
        setSaveOverlaps(
          (validation.overlaps ?? []).map((item) => ({ name: item.name, overlap_ha: item.overlap_ha })),
        );
        setSaveLocation({
          region: validation.location?.region ?? null,
          district: validation.location?.district ?? null,
        });
        setDraft({
          geometry,
          stats: {
            areaHa: validation.geometry?.area_ha ?? draft.stats?.areaHa ?? 0,
            perimeterM: validation.geometry?.perimeter_m ?? draft.stats?.perimeterM ?? 0,
            vertices: vertexCount,
          },
        });
        setPendingGeometry(geometry);
        setSaveError(null);
        setSaveOpen(true);
      } catch (error) {
        if (error instanceof ApiError && error.status === 0) {
          // Offline: keep the client-side estimate and let the server validate on save.
          setDraft({
            geometry,
            stats: { areaHa: draft.stats?.areaHa ?? 0, perimeterM: draft.stats?.perimeterM ?? 0, vertices: vertexCount },
          });
          setPendingGeometry(geometry);
          setSaveError(
            "No connection â€” this boundary has NOT been saved. Reconnect, then press â€œSave and analyseâ€. " +
              "Closing this dialog discards it.",
          );
          setSaveOpen(true);
          return;
        }
        const detail = error instanceof ApiError ? error.detail : null;
        const message =
          detail && typeof detail === "object" && "message" in (detail as Record<string, unknown>)
            ? String((detail as Record<string, unknown>).message)
            : error instanceof Error
              ? error.message
              : "This boundary could not be validated";
        notify(message, "error");
        setPendingGeometry(null);
      }
    },
    [draft.stats, notify],
  );

  const handleSaveFarm = useCallback(
    async (payload: Record<string, unknown>) => {
      if (!pendingGeometry) return;
      setSaveBusy(true);
      setSaveError(null);
      try {
        const response = await api.createFarm({
          name: payload.name ?? `Farm ${farms.length + 1}`,
          geometry: pendingGeometry,
          crop_id: payload.crop_id ?? null,
          planting_date: payload.planting_date ?? null,
          irrigation: payload.irrigation ?? "rainfed",
          notes: payload.notes ?? null,
          source: "drawn",
        });
        setSaveOpen(false);
        setPendingGeometry(null);
        await refreshFarms();
        await selectFarm(response.farm.id);
        notify("Farm saved. Analysis starts automatically.", "success");
      } catch (error) {
        const detail = error instanceof ApiError ? error.detail : null;
        const message =
          detail && typeof detail === "object" && "message" in (detail as Record<string, unknown>)
            ? String((detail as Record<string, unknown>).message)
            : error instanceof Error
              ? error.message
              : "Could not save the farm";
        setSaveError(message);
      } finally {
        setSaveBusy(false);
      }
    },
    [pendingGeometry, farms.length, refreshFarms, selectFarm, notify, runAnalysis],
  );

  const saveEdit = useCallback(async () => {
    if (!farm) return;
    const geometry = mapRef.current?.getDraft();
    if (!geometry) {
      notify("A boundary needs at least three vertices.", "error");
      return;
    }
    try {
      await api.updateFarm(farm.id, { geometry });
      const refreshed = await api.getFarm(farm.id);
      setFarm(refreshed.farm);
      await refreshFarms();
      endMode();
      notify("Boundary updated â€” re-running the analysis.", "success");
      void runAnalysis({ force: true, farmOverride: refreshed.farm });
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not update the boundary", "error");
    }
  }, [farm, refreshFarms, endMode, notify, runAnalysis]);

  const handleSplit = useCallback(
    async (line: Geometry) => {
      if (!farm) return;
      try {
        const result = await api.splitGeometry(farm.geometry, line);
        const letters = "ABCDEFGH";
        for (const [index, part] of result.parts.entries()) {
          await api.addField(farm.id, {
            name: `Field ${letters[index] ?? index + 1}`,
            geometry: part.geometry,
            crop_id: farm.crop_id ?? null,
          });
        }
        const refreshed = await api.getFarm(farm.id);
        setFarm(refreshed.farm);
        await refreshFarms();
        endMode();
        notify(`Split into ${result.parts.length} fields.`, "success");
      } catch (error) {
        notify(error instanceof Error ? error.message : "Could not split the boundary", "error");
        setMode("idle");
      }
    },
    [farm, refreshFarms, endMode, notify],
  );

  const handleDelete = useCallback(async () => {
    if (!farm) return;
    if (!window.confirm(`Delete â€œ${farm.name}â€ and all its records? This cannot be undone.`)) return;
    // Stop polling before the farm disappears, or the poll resurrects its panel.
    analysisAbort.current?.abort();
    analysisRun.current += 1;
    try {
      await api.deleteFarm(farm.id);
      setFarm(null);
      setSelectedFarmId(null);
      setAnalysis(null);
      await refreshFarms();
      notify("Farm deleted.", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not delete the farm", "error");
    }
  }, [farm, refreshFarms, notify]);

  const handleImport = useCallback(
    async (file: File) => {
      setCatalogBusy(true);
      try {
        const response = await api.importFarm(file);
        await refreshFarms();
        await selectFarm(response.farm.id);
        notify(`Imported ${response.format} boundary.`, "success");
      } catch (error) {
        const detail = error instanceof ApiError ? error.detail : null;
        const message =
          detail && typeof detail === "object" && "message" in (detail as Record<string, unknown>)
            ? String((detail as Record<string, unknown>).message)
            : error instanceof Error
              ? error.message
              : "Import failed";
        notify(message, "error");
      } finally {
        setCatalogBusy(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [refreshFarms, selectFarm, notify],
  );

  const createSample = useCallback(async () => {
    setCatalogBusy(true);
    setSampleError(null);
    try {
      const response = await api.createSample();
      await refreshFarms();
      await selectFarm(response.farm.id);
      notify("Sample boundary created â€” it uses real sourced data.", "success");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not create the sample farm";
      setSampleError(message);
      notify(message, "error");
    } finally {
      setCatalogBusy(false);
    }
  }, [refreshFarms, selectFarm, notify]);

  const analyzeField = useCallback(
    (fieldId: string | null) => {
      if (!farm) return;
      setActiveFieldId(fieldId);
      const geometry = fieldId ? farm.fields?.find((item) => item.id === fieldId)?.geometry ?? farm.geometry : farm.geometry;
      mapRef.current?.fitFarm(geometry);
      void runAnalysis({ force: true, fieldId, farmOverride: farm });
    },
    [farm, runAnalysis],
  );

  const selectCrop = useCallback(
    async (cropId: string) => {
      if (!farm) return;
      setCropsBusy(true);
      try {
        await api.updateFarm(farm.id, { crop_id: cropId });
        const refreshed = await api.getFarm(farm.id);
        setFarm(refreshed.farm);
        await refreshFarms();
        await runAnalysis({ force: true, cropId, farmOverride: refreshed.farm });
      } catch (error) {
        notify(error instanceof Error ? error.message : "Could not change the crop", "error");
      } finally {
        setCropsBusy(false);
      }
    },
    [farm, refreshFarms, runAnalysis, notify],
  );

  const handleAddSoilTest = useCallback(
    async (payload: Record<string, unknown>) => {
      if (!farm) return;
      await api.addSoilTest(farm.id, payload);
      const refreshed = await api.getFarm(farm.id);
      setFarm(refreshed.farm);
      notify("Soil test recorded â€” guidance now uses the measured value.", "success");
      void runAnalysis({ force: true, farmOverride: refreshed.farm });
    },
    [farm, notify, runAnalysis],
  );

  const handleAddRecord = useCallback(
    async (payload: Record<string, unknown>) => {
      if (!farm) return;
      await api.addRecord(farm.id, payload);
      const refreshed = await api.getFarm(farm.id);
      setFarm(refreshed.farm);
      notify("Record saved.", "success");
    },
    [farm, notify],
  );

  const handleExport = useCallback(
    async (format: "geojson" | "kml" | "csv" | "pdf") => {
      if (!farm) return;
      if (format === "pdf" && !analysis) {
        notify("Run an analysis before exporting a PDF report.", "error");
        return;
      }
      try {
        const blob = await downloadExport(farm.id, format);
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${(farm.name || "farm").replace(/[^\w.-]+/g, "_")}.${format}`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        // Revoking in the same tick cancels the download in some browsers.
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
      } catch (error) {
        notify(error instanceof Error ? error.message : "The export failed.", "error");
      }
    },
    [farm, analysis, notify],
  );

  const handleExportAll = useCallback(() => {
    const payload = { farms, exported_at: new Date().toISOString() };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "ghana-farm-intelligence-farms.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }, [farms]);

  const ensureLayer = useCallback(
    async (layerId: string) => {
      if (!farm || !EVIDENCE_LAYER_IDS.has(layerId)) return;
      if (layerChips[layerId] || layerRequests.current.has(layerId)) return;
      const epoch = layerEpoch.current;
      layerRequests.current.add(layerId);
      setLayers((current) => current.map((layer) => (layer.id === layerId ? { ...layer, loading: true } : layer)));
      try {
        const chip = await api.farmLayer(farm.id, layerId, activeFieldId);
        // The farm or field may have changed while this was in flight; a stale
        // chip would paint one boundary's raster over another.
        if (layerEpoch.current !== epoch) return;
        if (chip.available && chip.url && chip.corners) {
          setLayerChips((current) => ({ ...current, [layerId]: chip }));
          setLayers((current) =>
            current.map((layer) =>
              layer.id === layerId
                ? {
                    ...layer,
                    loading: false,
                    available: true,
                    legend: chip.legend?.length ? chip.legend : layer.legend,
                    description: chip.note ?? layer.description,
                    resolutionM: chip.resolution_m,
                    valueRange: chip.value_range ?? null,
                    unit: chip.unit,
                    unavailableReason: undefined,
                  }
                : layer,
            ),
          );
        } else {
          setLayers((current) =>
            current.map((layer) =>
              layer.id === layerId
                ? { ...layer, loading: false, available: false, unavailableReason: chip.reason ?? "No data for this field" }
                : layer,
            ),
          );
        }
      } catch (error) {
        setLayers((current) =>
          current.map((layer) =>
            layer.id === layerId
              ? { ...layer, loading: false, unavailableReason: "This layer could not be rendered for the field." }
              : layer,
          ),
        );
        notify(error instanceof Error ? error.message : "Could not render this layer", "error");
      } finally {
        layerRequests.current.delete(layerId);
      }
    },
    [farm, activeFieldId, layerChips, notify],
  );

  useEffect(() => {
    setSatelliteOn(false);
  }, [farm?.id]);

  const toggleSatellite = useCallback(() => {
    if (!farm) {
      notify("Select a farm first, then open the satellite view.", "error");
      return;
    }
    if (!online) {
      notify("The satellite basemap needs a connection; the vector map still works offline.", "error");
      return;
    }
    setSatelliteOn((value) => {
      const next = !value;
      if (next) {
        // Sentinel-2 cloudless is a 10 m mosaic; at field-zoom it is only upscaled
        // mush, so show it at a regional scale where the mosaic is legible.
        const current = mapRef.current?.map()?.getZoom() ?? 14;
        if (current > 14.2) {
          mapRef.current?.flyTo(farm.centroid_lat, farm.centroid_lon, 14.2);
        }
      }
      return next;
    });
  }, [farm, online, notify]);

  const updateLayer = useCallback(
    (id: string, patch: Partial<LayerState>) => {
      setLayers((current) => current.map((layer) => (layer.id === id ? { ...layer, ...patch } : layer)));
      if (patch.visible && EVIDENCE_LAYER_IDS.has(id)) void ensureLayer(id);
    },
    [ensureLayer],
  );

  useEffect(() => {
    // Any in-flight layer request now belongs to a boundary nobody is looking at.
    layerEpoch.current += 1;
    setLayerChips({});
    layerRequests.current.clear();
    setLayers((current) =>
      current.map((layer) =>
        EVIDENCE_LAYER_IDS.has(layer.id)
          ? { ...layer, available: true, loading: false, legend: undefined, resolutionM: undefined, valueRange: null, unit: undefined, unavailableReason: undefined }
          : layer,
      ),
    );
  }, [farm?.id, activeFieldId]);

  useEffect(() => {
    if (!farm) return;
    const visible = layers.filter((layer) => EVIDENCE_LAYER_IDS.has(layer.id) && layer.visible).map((layer) => layer.id);
    visible.forEach((id) => void ensureLayer(id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [farm?.id, activeFieldId]);

  const toggleLayers = useCallback(() => {
    setLayersOpen((value) => {
      const next = !value;
      if (next && window.innerWidth <= 980) setPanelOpen(false);
      return next;
    });
  }, []);

  const togglePanel = useCallback(() => {
    setPanelOpen((value) => {
      const next = !value;
      if (next && window.innerWidth <= 980) setLayersOpen(false);
      return next;
    });
  }, []);

  const handleMapClick = useCallback((lat: number, lon: number) => {
    void api
      .reversePlace(lat, lon)
      .then((result) => {
        const label = result.place
          ? `${result.place.name} (${result.place.distance_km} km)`
          : result.district ?? result.region ?? "Ghana";
        setMapNotice(`Map centre: ${label}, ${lat.toFixed(4)}, ${lon.toFixed(4)}`);
      })
      .catch(() => setMapNotice(`${lat.toFixed(4)}, ${lon.toFixed(4)}`));
  }, []);

  const openSettings = useCallback(() => {
    setSettingsOpen(true);
    if (!providers.length) {
      setProvidersLoading(true);
      api
        .providers()
        .then((payload) => setProviders(payload.providers as unknown as Array<Record<string, unknown>>))
        .catch(() => undefined)
        .finally(() => setProvidersLoading(false));
    }
    void offline.usage().then(setUsage).catch(() => undefined);
  }, [providers.length]);

  const clearOffline = useCallback(async () => {
    await offline.clearAnalyses();
    setUsage(await offline.usage().catch(() => null));
    notify("Saved analyses cleared from this device.", "success");
  }, [notify]);

  const dataAge = analysis ? relativeAge(analysis.generated_at) : null;
  const showOnboarding = !booting && farms.length === 0 && !saveOpen;

  const overlays: MapOverlay[] = useMemo(() => {
    const list: MapOverlay[] = [];
    const latest = analysis?.sections.vegetation?.data?.latest;
    if (latest?.chips && latest.image_corners?.length === 4) {
      const chips: Array<[string, string | undefined]> = [
        ["true-color", latest.chips.true_color],
        ["ndvi", latest.chips.ndvi],
        ["ndmi", latest.chips.ndmi],
      ];
      for (const [id, url] of chips) {
        if (!url) continue;
        const layer = layers.find((item) => item.id === id);
        list.push({
          id,
          url,
          corners: latest.image_corners,
          visible: Boolean(layer?.visible),
          opacity: layer?.opacity ?? 0.8,
        });
      }
    }
    for (const [id, chip] of Object.entries(layerChips)) {
      if (!chip.url || !chip.corners || chip.corners.length !== 4) continue;
      const layer = layers.find((item) => item.id === id);
      list.push({
        id,
        url: chip.url,
        corners: chip.corners,
        visible: Boolean(layer?.visible),
        opacity: layer?.opacity ?? 0.75,
      });
    }
    return list;
  }, [analysis, layerChips, layers]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      window.location.reload();
    }
  }, []);

  if (accountsEnabled && !authUser) {
    return (
      <div className="gate">
        <form
          className="gate__card"
          onSubmit={async (event) => {
            event.preventDefault();
            setLoginBusy(true);
            setLoginError(null);
            try {
              await api.login(loginName.trim(), loginPassword);
              setLoginPassword("");
              window.location.reload();
            } catch (error) {
              setLoginError(error instanceof Error ? error.message : "Sign-in failed.");
              setLoginBusy(false);
            }
          }}
        >
          <h1>Ghana Farm Intelligence</h1>
          <p>Sign in with the account an administrator created for you.</p>
          {loginError ? (
            <div className="banner banner--warn" role="alert">
              {loginError}
            </div>
          ) : null}
          <input
            value={loginName}
            onChange={(event) => setLoginName(event.target.value)}
            placeholder="Username"
            aria-label="Username"
            autoComplete="username"
            autoFocus
            required
          />
          <input
            type="password"
            value={loginPassword}
            onChange={(event) => setLoginPassword(event.target.value)}
            placeholder="Password"
            aria-label="Password"
            autoComplete="current-password"
            required
          />
          <button
            type="submit"
            className="button button--primary"
            disabled={loginBusy || !loginName.trim() || !loginPassword}
          >
            {loginBusy ? "Signing inâ€¦" : "Sign in"}
          </button>
          <p className="tiny muted">Accounts are created by an administrator. There is no self-signup.</p>
        </form>
      </div>
    );
  }

  if (gateOpen) {
    return (
      <div className="gate">
        <form
          className="gate__card"
          onSubmit={(event) => {
            event.preventDefault();
            setAccessToken(gateToken.trim());
            window.location.reload();
          }}
        >
          <h1>Ghana Farm Intelligence</h1>
          <p>This deployment is protected. Enter its access token to continue.</p>
          <input
            type="password"
            autoFocus
            value={gateToken}
            onChange={(event) => setGateToken(event.target.value)}
            placeholder="Access token"
            aria-label="Access token"
            autoComplete="current-password"
          />
          <button type="submit" className="button button--primary" disabled={!gateToken.trim()}>
            Unlock
          </button>
          <p className="tiny muted">
            One shared secret for this deployment, not a personal account. It is stored in this browser only.
          </p>
        </form>
      </div>
    );
  }

  return (
    <div className={`app ${panelOpen ? "app--panel" : ""} ${layersOpen ? "app--layers" : ""}`}>
      <TopBar
        farms={farms}
        selectedFarmId={selectedFarmId}
        onSelectFarm={(id) => void selectFarm(id)}
        onSearch={() => setSearchOpen(true)}
        onSettings={openSettings}
        online={online}
        theme={theme}
        onToggleTheme={() => setTheme(theme === "dark" ? "daylight" : "dark")}
        dataAge={dataAge}
        onToggleLayers={toggleLayers}
        onTogglePanel={togglePanel}
        layersOpen={layersOpen}
        panelOpen={panelOpen}
      />

      <div className="workspace">
        {layersOpen ? (
          <LayersPanel layers={layers} onChange={updateLayer} onClose={() => setLayersOpen(false)} analysis={analysis} />
        ) : null}

        <main className="map-area">
          <MapView
            ref={mapRef}
            farms={farms}
            selectedFarmId={selectedFarmId}
            farm={farm}
            overlays={overlays}
            satellite={satelliteOn}
            mode={mode}
            layers={layers}
            theme={theme}
            onSelectFarm={(id) => void selectFarm(id)}
            onDraftChange={handleDraftChange}
            onDraftComplete={(geometry) => void handleDraftComplete(geometry)}
            onSplit={(line) => void handleSplit(line)}
            onGpsStatus={setGps}
            onMapClick={handleMapClick}
          />

          <div className="map-toolbar" role="toolbar" aria-label="Farm boundary tools">
            {mode === "idle" ? (
              <>
                <button type="button" className="map-tool" onClick={beginDraw} title="Draw farm (d)" aria-label="Draw farm boundary">
                  âœŽ <span>Draw farm</span>
                </button>
                <button type="button" className="map-tool" onClick={startGpsWalk} title="Record a GPS walk (g)" aria-label="Record a GPS boundary walk">
                  â—Ž <span>GPS walk</span>
                </button>
                <button
                  type="button"
                  className="map-tool"
                  onClick={() => fileInputRef.current?.click()}
                  title="Import GeoJSON, KML or GPX"
                  aria-label="Import GeoJSON, KML or GPX"
                >
                  â‡ª <span>Import</span>
                </button>
                {farm ? (
                  <>
                    <button type="button" className="map-tool" onClick={beginEdit} title="Edit vertices (e)" aria-label="Edit boundary vertices">
                      âœ¥ <span>Edit</span>
                    </button>
                    <button type="button" className="map-tool" onClick={beginSplit} title="Split into fields" aria-label="Split farm into fields">
                      â§¸ <span>Split</span>
                    </button>
                    <button
                      type="button"
                      className={`map-tool map-tool--satellite ${satelliteOn ? "is-active" : ""}`}
                      onClick={toggleSatellite}
                      title="Satellite basemap â€” Sentinel-2 cloudless 2025 (EOX)"
                      aria-label="Toggle satellite view"
                      aria-pressed={satelliteOn}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
                        <circle cx="12" cy="12" r="8" />
                        <path d="M4 12h16M12 4a13 13 0 0 1 0 16M12 4a13 13 0 0 0 0 16" />
                      </svg>{" "}
                      <span>Satellite</span>
                    </button>
                  </>
                ) : null}
                <button
                  type="button"
                  className={`map-tool ${terrain ? "is-active" : ""}`}
                  onClick={() => {
                    const next = !terrain;
                    setTerrain(next);
                    mapRef.current?.setTerrain(next);
                  }}
                  title="Toggle 3D terrain"
                  aria-label="Toggle 3D terrain"
                  aria-pressed={terrain}
                >
                  3D <span>Terrain</span>
                </button>
              </>
            ) : null}

            {mode === "draw" ? (
              <div className="draw-bar" role="status" aria-live="polite">
                <span className="draw-bar__stat">
                  <strong>{draft.stats?.vertices ?? 0}</strong> vertices Â·{" "}
                  {draft.stats?.areaHa ? `${draft.stats.areaHa.toFixed(2)} ha` : "no area yet"}
                </span>
                <button type="button" className="button button--ghost button--small" onClick={() => mapRef.current?.undo()}>
                  Undo
                </button>
                <button type="button" className="button button--ghost button--small" onClick={() => mapRef.current?.redo()}>
                  Redo
                </button>
                <button type="button" className="button button--primary button--small" onClick={() => mapRef.current?.finishDraw()}>
                  Finish
                </button>
                <button type="button" className="button button--ghost button--small" onClick={endMode}>
                  Cancel
                </button>
              </div>
            ) : null}

            {mode === "edit" ? (
              <div className="draw-bar">
                <span className="draw-bar__stat">Drag vertices to reshape the boundary</span>
                <button type="button" className="button button--primary button--small" onClick={() => void saveEdit()}>
                  Save vertices
                </button>
                <button type="button" className="button button--ghost button--small" onClick={endMode}>
                  Cancel
                </button>
              </div>
            ) : null}

            {mode === "split" ? (
              <div className="draw-bar">
                <span className="draw-bar__stat">Tap two points to draw the split line</span>
                <button type="button" className="button button--ghost button--small" onClick={() => mapRef.current?.resetSplit()}>
                  Reset line
                </button>
                <button type="button" className="button button--ghost button--small" onClick={endMode}>
                  Cancel
                </button>
              </div>
            ) : null}

            {mode === "gps" ? (
              <div className="draw-bar" role="status" aria-live="polite">
                <span className="draw-bar__stat">
                  GPS walk Â· {gps.points} points{gps.accuracy ? ` Â· Â±${Math.round(gps.accuracy)} m` : ""}
                  {gps.error ? ` Â· ${gps.error}` : ""}
                </span>
                <button
                  type="button"
                  className="button button--primary button--small"
                  disabled={gps.points < 3}
                  onClick={() => {
                    const geometry = mapRef.current?.finishGps() ?? null;
                    setMode("idle");
                    setMapNotice(null);
                    if (geometry) {
                      void handleDraftComplete(geometry);
                    } else {
                      notify("Walk at least three GPS points before finishing.", "error");
                    }
                  }}
                >
                  Finish walk
                </button>
                <button type="button" className="button button--ghost button--small" onClick={endMode}>
                  Cancel
                </button>
              </div>
            ) : null}
          </div>

          {mapNotice ? (
            <div className="map-notice" role="status">
              {mapNotice}
              <button type="button" className="icon-button" onClick={() => setMapNotice(null)} aria-label="Dismiss map message">
                âœ•
              </button>
            </div>
          ) : null}

          {satelliteOn ? (
            <div className="map-satellite-note" role="status">
              <strong>Satellite basemap</strong> Sentinel-2 cloudless 2025 Â· 10 m Â· Â© EOX (CC BY-SA 4.0)
            </div>
          ) : null}

          {farm ? (
            <div className="map-context" aria-label="Location context">
              <span className="map-context__district">{analysis?.farm.district || farm.district || "District unknown"}</span>
              <span className="map-context__region">{analysis?.farm.region || farm.region || "Region unknown"}</span>
              {analysis?.farm.zone ? <span className="map-context__zone">{analysis.farm.zone.name}</span> : null}
            </div>
          ) : null}

          {!online ? (
            <div className="offline-banner" role="status">
              Offline â€” showing saved data{farmsSavedAt ? ` from ${relativeAge(farmsSavedAt)}` : ""}. Live updates need
              connectivity.
            </div>
          ) : null}

          {analysis ? (
            <Timeline analysis={analysis} range={timelineRange} onRange={setTimelineRange} />
          ) : null}
        </main>

        {panelOpen ? (
          <FarmPanel
            farm={farm}
            analysis={analysis}
            job={job}
            partial={partial}
            tab={tab}
            onTab={setTab}
            onRefresh={() => void runAnalysis({ force: true })}
            onAnalyze={() => void runAnalysis()}
            onAnalyzeField={analyzeField}
            onExport={handleExport}
            onEdit={beginEdit}
            onDelete={() => void handleDelete()}
            onSplit={beginSplit}
            onAddSoilTest={handleAddSoilTest}
            onAddRecord={handleAddRecord}
            crops={crops}
            onSelectCrop={(cropId) => void selectCrop(cropId)}
            offlineSavedAt={online ? null : analysisSavedAt}
            onClose={() => setPanelOpen(false)}
            busy={busy}
            cropsBusy={cropsBusy}
            activeFieldId={activeFieldId}
          />
        ) : null}
      </div>

      {showOnboarding ? (
        <Onboarding
          onSearch={() => setSearchOpen(true)}
          onDraw={beginDraw}
          onImport={() => fileInputRef.current?.click()}
          onGps={startGpsWalk}
          onSample={() => void createSample()}
          onSettings={openSettings}
          busy={catalogBusy}
          error={sampleError}
        />
      ) : null}

      <SearchOverlay
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onSelect={(result) => {
          mapRef.current?.flyTo(result.lat, result.lon, result.kind === "region" ? 8 : 13);
          setMapNotice(`${result.name}${result.district ? ` Â· ${result.district}` : ""}${result.region ? ` Â· ${result.region}` : ""}`);
          if (window.innerWidth <= 980) setPanelOpen(false);
        }}
        onDrawHere={() => {
          setSearchOpen(false);
          const map = mapRef.current?.map();
          const center = map?.getCenter();
          if (center) mapRef.current?.flyTo(center.lat, center.lng, Math.max(15, map?.getZoom() ?? 15));
          beginDraw();
        }}
      />

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        theme={theme}
        onTheme={setTheme}
        units={units}
        onUnits={setUnits}
        language={language}
        onLanguage={setLanguage}
        providers={providers}
        providersLoading={providersLoading}
        usage={usage}
        onClearOffline={() => void clearOffline()}
        onExportAll={handleExportAll}
        me={authUser}
        onSignOut={() => void signOut()}
      />

      <SaveFarmModal
        open={saveOpen}
        geometry={pendingGeometry}
        stats={draft.stats}
        defaultName={saveLocation.district ? `Farm in ${saveLocation.district}` : `Farm ${farms.length + 1}`}
        onSave={handleSaveFarm}
        onClose={() => {
          setSaveOpen(false);
          setPendingGeometry(null);
          setDraft({ geometry: null, stats: null });
        }}
        region={saveLocation.region}
        district={saveLocation.district}
        overlaps={saveOverlaps}
        busy={saveBusy}
        error={saveError}
      />

      <input
        ref={fileInputRef}
        type="file"
        accept=".geojson,.json,.kml,.gpx,application/geo+json,application/json"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void handleImport(file);
        }}
      />

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
      {catalogBusy ? (
        <div className="busy-veil" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          Workingâ€¦
        </div>
      ) : null}
    </div>
  );
}
