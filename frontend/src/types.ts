export type DataStatus = "ok" | "stale" | "unavailable" | "error" | "demo";

export interface Source {
  provider_id: string;
  dataset: string;
  url: string;
  licence: string;
  resolution?: string | null;
  period?: string | null;
  method?: string | null;
  limitations?: string | null;
  access_method?: string | null;
  key_required?: boolean | null;
  status?: DataStatus;
  fetched_at?: string;
  section?: string;
}

export interface Section<T = Record<string, unknown>> {
  key: string;
  status: DataStatus;
  data: T;
  sources: Source[];
  retrieved_at: string;
  message?: string | null;
  warnings: string[];
}

export interface Geometry {
  type: "Polygon" | "MultiPolygon";
  coordinates: number[][][] | number[][][][];
}

export interface Farm {
  id: string;
  name: string;
  region?: string | null;
  district?: string | null;
  zone?: string | null;
  crop_id?: string | null;
  crop_variety?: string | null;
  planting_date?: string | null;
  irrigation?: string | null;
  notes?: string | null;
  geometry: Geometry;
  area_ha: number;
  perimeter_m: number;
  centroid_lat: number;
  centroid_lon: number;
  source: string;
  is_demo: boolean;
  created_at: string;
  updated_at: string;
  fields?: FarmField[];
  soil_tests?: SoilTest[];
  records?: FarmRecord[];
  field_count?: number;
}

export interface FarmField {
  id: string;
  farm_id: string;
  name: string;
  crop_id?: string | null;
  planting_date?: string | null;
  irrigation?: string | null;
  geometry: Geometry;
  area_ha: number;
  perimeter_m: number;
  created_at: string;
  updated_at: string;
}

export interface SoilTest {
  id: string;
  sampled_on: string;
  lab_name?: string | null;
  ph?: number | null;
  soc_gkg?: number | null;
  total_n_gkg?: number | null;
  p_mgkg?: number | null;
  k_mgkg?: number | null;
  cec_cmolkg?: number | null;
  texture_class?: string | null;
}

export interface FarmRecord {
  id: string;
  kind: string;
  title?: string | null;
  notes?: string | null;
  payload?: Record<string, unknown> | null;
  recorded_at: string;
}

export interface EvidenceItem {
  label: string;
  value: string | number | null;
  source: string;
  detail?: string | null;
}

export interface Recommendation {
  id: string;
  title: string;
  action: string;
  why: string;
  severity: "warning" | "watch" | "info" | "good";
  priority: number;
  evidence: EvidenceItem[];
  confidence: string;
  limitations: string;
  sources: Array<{ title?: string; url?: string; publisher?: string }>;
  category: string;
}

export interface CropComponent {
  name: string;
  score: number | null;
  weight: number;
  farm_value: string | number | null;
  requirement: string | null;
  source: string;
  note?: string | null;
  available: boolean;
}

export interface CropEvaluation {
  id: string;
  name: string;
  scientific_name?: string;
  local_names: Record<string, string>;
  perennial: boolean;
  category: string;
  category_label: string;
  category_uncapped?: string | null;
  score: number | null;
  score_range?: [number, number] | null;
  confidence: string;
  confidence_note?: string | null;
  components: CropComponent[];
  deciding_factors: Array<{
    factor: string;
    farm_value: string | number | null;
    requirement: string | null;
    score: number | null;
    source: string;
    note?: string | null;
  }>;
  constraints: string[];
  missing_inputs: string[];
  planting_window: {
    season: string;
    label: string;
    planting_window: { start: string; end: string; label: string };
    harvest_window: { start: string; end: string; label: string };
    state: string;
    days_to_window: number | null;
    start_iso: string;
    end_iso: string;
  } | null;
  harvest_window: { start: string; end: string; label: string } | null;
  cycle_days: { min: number; max: number; typical: number } | null;
  current_stage: { name: string; days_since_planting: number; notes?: string | null } | null;
  zone_fit_note?: string | null;
  zone_warning?: boolean;
  common_stresses: string[];
  management_note?: string | null;
  water_note?: string | null;
  fertility_note?: string | null;
  limitations?: string | null;
  evidence_quality?: string;
  sources: Array<{ id?: string; title?: string; publisher?: string; url?: string; licence?: string }>;
  seasonal_rainfall_mm?: number | null;
}

export interface AnalysisSectionMap {
  weather?: Section<{
    current: Record<string, number | string | null>;
    daily: Array<Record<string, number | string | null>>;
    hourly: Array<Record<string, number | string | null>>;
    analytics: {
      next_7_days: {
        precipitation_mm: number;
        et0_mm: number;
        rain_days: number;
        water_balance_mm: number;
        temperature_max_c: number | null;
        temperature_min_c: number | null;
      };
      dry_spell_days: number;
      heat_days: string[];
      heavy_rain_days: Array<{ date: string; mm: number }>;
      best_3_day_rain_window: { start: string; total: number; rain_days: number } | null;
    };
    grid: { latitude: number; longitude: number; elevation_m: number; nominal_resolution_km: number };
    grid_distance_km: number | null;
    forecast_days: number;
    notes: string[];
  }>;
  soil?: Section<{
    modelled?: {
      depths: Record<string, { label: string; unit: string; values: Record<string, Record<string, number | null>> }>;
      profile_0_30: Record<
        string,
        {
          value: number;
          unit: string;
          label: string;
          valid_fraction: number;
          sub_field_detail: boolean;
          resolution: { nominal_m: number };
          pixels_in_polygon: number;
          pixel_area_ha: number;
          single_grid_cell: boolean;
        }
      >;
      texture_class: string | null;
      texture_fractions: { sand: number | null; clay: number | null; silt: number | null };
      acidity_class?: string;
      ph?: number;
      uncertainty: Record<string, { depths: Record<string, { q05: number; q95: number }>; note: string }>;
      coverage: {
        valid_fraction: number;
        resolution: { nominal_m: number };
        pixels_in_polygon: number;
        pixel_area_ha: number;
        sub_field_detail: boolean;
        single_grid_cell: boolean;
      };
      limitations: string[];
    };
    measured?: Record<string, number | string | null> | null;
    precedence?: string;
    provider_note?: string;
  }>;
  rainfall?: Section<{
    latest_available: string;
    lag_days: number;
    today: string;
    windows: Record<string, { start: string; end: string; total_mm: number; days: number }>;
    anomalies: Record<
      string,
      {
        observed_mm: number;
        baseline_mm: number | null;
        anomaly_mm?: number;
        percent_of_normal: number | null;
        label: string;
        baseline_period?: string;
      }
    >;
    daily_series: Array<{ date: string; mm: number | null }>;
    monthly_series: Array<{ year: number; month: number; mm: number | null }>;
    recent_30_days: {
      rainy_days: number;
      heavy_rain_days: number;
      longest_dry_spell_days: number;
      days: number;
    };
    climatology: { monthly_mean_mm: Record<string, number | null>; annual_total_mm: number; period: string } | null;
    notes: string[];
  }>;
  vegetation?: Section<{
    latest: {
      id: string;
      date: string;
      datetime: string;
      scene_cloud_cover: number | null;
      cloud_free_fraction: number;
      valid_fraction: number;
      ndvi: { mean: number | null; median: number | null; sd: number | null; p10: number | null; p90: number | null };
      ndmi: { mean: number | null; median: number | null; sd: number | null } | null;
      chips?: { true_color: string; ndvi: string; ndmi: string };
      image_corners: number[][];
      spatial_pattern?: {
        field_mean_ndvi: number;
        field_sd_ndvi: number;
        low_vigour_share: number;
        quadrants_lowest_first: Array<{ quadrant: string; mean_ndvi: number }>;
        note: string;
      } | null;
      resolution: { nominal_m: number };
      pixels: number;
    };
    history: Array<{
      date: string;
      scene_id: string;
      ndvi_mean: number | null;
      ndvi_median: number | null;
      cloud_free_fraction: number;
      valid_fraction: number;
    }>;
    trend_per_scene: number | null;
    change_vs_recent_median: number | null;
    scenes_evaluated: number;
    scenes_available: number;
    search_window: { start: string; end: string };
    notes: string[];
  }>;
  terrain?: Section<{
    elevation_m: { mean: number; min: number; max: number; p10: number; p90: number };
    slope_percent: { mean: number; min: number; max: number; p10: number; p90: number };
    slope_degrees: { mean: number };
    aspect_degrees: number | null;
    aspect_label: string | null;
    flat_share: number;
    steep_share_over_15pct: number;
    erosion_screening: { level: string; basis: string; note: string };
    resolution: { nominal_m: number };
    notes: string[];
  }>;
  landcover?: Section<{
    classes: Array<{ code: number; name: string; color: string; share: number }>;
    cropland_share: number;
    tree_cover_share: number;
    year: number;
    is_historical_baseline: boolean;
    resolution: { nominal_m: number };
    notes: string[];
  }>;
  water?: Section<{
    reason?: string;
    provider_status?: { reachable: boolean; message: string; status_code?: number };
    not_shown?: string[];
    substitute_used?: string;
  }>;
  crops?: Section<{
    selected: CropEvaluation | null;
    selected_id: string | null;
    ranking: CropEvaluation[];
    all_evaluations: Array<{
      id: string;
      name: string;
      score: number | null;
      category: string;
      category_label: string;
      confidence: string;
      perennial: boolean;
    }>;
    insufficient: string[];
    missing_inputs: string[];
    method: { weights: Record<string, number>; note: string; category_bands: Array<{ min_score: number; label: string }> };
    inputs: Record<string, unknown>;
    notes: string[];
  }>;
  calendar?: Section<{
    milestones: Array<{
      name: string;
      start_day: number;
      end_day: number;
      start_date: string | null;
      end_date: string | null;
      notes: string | null;
      status: string;
    }>;
    tasks: Array<{
      title: string;
      due_date: string;
      window_end: string | null;
      notes: string;
      kind: string;
      days_until: number | null;
    }>;
    alerts: Array<{ title: string; due_date: string; notes: string; kind: string; days_until: number }>;
    crop_name?: string;
    planting_date: string | null;
    season?: CropEvaluation["planting_window"];
    notes: string[];
  }>;
  recommendations?: Section<{
    recommendations: Recommendation[];
    top_actions: Recommendation[];
    counts: Record<string, number>;
  }>;
}

export interface Analysis {
  analysis_id: string;
  version: number;
  generated_at: string;
  elapsed_ms: number;
  cached?: boolean;
  farm: {
    id: string;
    name: string;
    geometry: Geometry;
    area_ha: number;
    perimeter_m: number;
    centroid: { lat: number; lon: number };
    bbox: { west: number; south: number; east: number; north: number };
    vertex_count: number;
    geometry_label: string;
    field_id: string | null;
    is_demo: boolean;
    source: string;
    crop_id: string | null;
    planting_date: string | null;
    irrigation: string | null;
    management_level: string;
    district: string | null;
    region: string | null;
    zone: {
      id: string;
      name: string;
      annual_rainfall_mm: number | null;
      season_type: string;
      confidence: string;
      method: string;
      description: string;
      note: string;
    };
    annual_rainfall_mm: number | null;
    location_match: string;
  };
  sections: AnalysisSectionMap;
  summary: {
    top_actions: Recommendation[];
    counts: Record<string, number>;
    data_status: {
      sections: Record<string, number>;
      unavailable: string[];
      stale: string[];
      core_complete: boolean;
      provider_health: Record<string, { status: string; checked_at: string; latency_ms: number | null }>;
    };
    headline: Record<string, string | number | null>;
  };
  provenance: Source[];
  sources_registry: Array<Record<string, unknown>>;
  disclaimer: string;
}

export interface Job {
  id: string;
  farm_id: string | null;
  kind: string;
  status: "queued" | "running" | "complete" | "failed";
  progress: number;
  message: string | null;
  result: Analysis | { sections: Partial<AnalysisSectionMap>; partial: true } | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlaceResult {
  id: string;
  name: string;
  type: string;
  lat: number | null;
  lon: number | null;
  region: string | null;
  district: string | null;
  population: number | null;
  kind: string;
}

export interface CropSummary {
  id: string;
  name: string;
  scientific_name: string;
  local_names: Record<string, string>;
  perennial: boolean;
  cycle_days: { min: number; max: number; typical: number };
  water: { seasonal_rainfall_mm: [number, number]; drought_tolerance: string };
  soil: { ph: [number, number]; ph_optimal: [number, number]; texture_preferred: string[] };
  temperature_c: { mean_min: number; mean_max: number; optimal: [number, number] };
  slope_max_percent: number;
  zones: Record<string, string>;
  sources: Array<{ title: string; url: string; publisher: string }>;
  limitations: string;
  common_stresses: string[];
  evidence_quality: string;
}

export interface ProviderSpec {
  id: string;
  name: string;
  category: string;
  variables: string;
  resolution: string;
  temporal: string;
  coverage: string;
  licence: string;
  licence_notes: string;
  access: string;
  key_required: boolean;
  status: string;
  homepage: string;
  integration_notes: string;
  limitations: string;
  verification: string;
  commercial_note?: string | null;
  verified_on: string;
  last_checked?: { status: string; checked_at: string; latency_ms: number | null; message: string | null } | null;
}

export interface LayerState {
  id: string;
  name: string;
  visible: boolean;
  opacity: number;
  kind: "imagery" | "vector" | "derived";
  description: string;
  legend?: Array<{ color: string; label: string }>;
  sourceId?: string;
  unavailableReason?: string;
  available: boolean;
  loading?: boolean;
  resolutionM?: number;
  valueRange?: [number, number] | null;
  unit?: string;
}

export interface LayerChip {
  available: boolean;
  layer_id: string;
  title?: string;
  url?: string;
  corners?: number[][];
  legend?: Array<{ color: string; label: string }>;
  resolution_m?: number;
  unit?: string;
  source?: {
    provider_id: string;
    dataset: string;
    licence: string;
    url: string;
    resolution_m: number;
  };
  note?: string;
  pixels?: number;
  value_range?: [number, number] | null;
  generated_at?: string;
  reason?: string;
  cached?: boolean;
}

export interface MapOverlay {
  id: string;
  url: string;
  corners: number[][];
  visible: boolean;
  opacity: number;
}

export interface SatelliteChip {
  available: boolean;
  url?: string;
  corners?: number[][];
  scene_id?: string;
  scene_date?: string;
  scene_cloud_cover?: number | null;
  cloud_fraction_over_area?: number;
  platform?: string;
  resolution_m?: number;
  buffer_m?: number;
  pixels?: [number, number];
  source?: {
    provider_id: string;
    dataset: string;
    licence: string;
    url: string;
    resolution_m: number;
  };
  note?: string;
  generated_at?: string;
  reason?: string;
  farm_id?: string;
  geometry_label?: string;
}

export interface AccountUser {
  id: string;
  username: string;
  is_admin: boolean;
  created_at: string;
  updated_at?: string;
  farm_count?: number;
}

export interface AuthMe {
  authenticated: boolean;
  accounts_enabled: boolean;
  user: AccountUser | null;
  posture: "accounts" | "token" | "open";
}
