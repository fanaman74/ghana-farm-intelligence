import type {
  Analysis,
  CropSummary,
  Farm,
  FarmField,
  Job,
  LayerChip,
  PlaceResult,
  ProviderSpec,
  SatelliteChip,
  SoilTest,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(init?.headers ?? {}),
      },
    });
  } catch (error) {
    throw new ApiError(0, "Network unavailable. Check your connection.", error);
  }
  if (!response.ok) {
    let detail: unknown = null;
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload.detail ?? payload;
      if (typeof payload.detail === "string") message = payload.detail;
      else if (payload.detail?.message) message = payload.detail.message;
      else if (payload.message) message = payload.message;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(response.status, message, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; time: { time: string; date: string } }>("/api/health"),
  meta: () => request<Record<string, unknown>>("/api/meta"),

  listFarms: () => request<{ farms: Farm[]; count: number }>("/api/farms"),
  getFarm: (id: string) => request<{ farm: Farm }>(`/api/farms/${id}`),
  createFarm: (body: Record<string, unknown>) =>
    request<{ farm: Farm; validation: Record<string, unknown>; overlaps: unknown[]; warnings: string[] }>(
      "/api/farms",
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateFarm: (id: string, body: Record<string, unknown>) =>
    request<{ farm: Farm; warnings: string[] }>(`/api/farms/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteFarm: (id: string) => request<void>(`/api/farms/${id}`, { method: "DELETE" }),
  createSample: () => request<{ farm: Farm }>("/api/farms/sample", { method: "POST" }),
  addField: (farmId: string, body: Record<string, unknown>) =>
    request<{ field: FarmField; warnings: string[] }>(`/api/farms/${farmId}/fields`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteField: (farmId: string, fieldId: string) =>
    request<void>(`/api/farms/${farmId}/fields/${fieldId}`, { method: "DELETE" }),
  updateField: (farmId: string, fieldId: string, body: Record<string, unknown>) =>
    request<{ field: FarmField }>(`/api/farms/${farmId}/fields/${fieldId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  validateGeometry: (geometry: unknown) =>
    request<{
      valid: boolean;
      geometry?: { area_ha: number; perimeter_m: number; centroid: { lat: number; lon: number }; warnings: string[] };
      error?: { code: string; message: string };
      overlaps: Array<{ id: string; name: string; overlap_ha: number }>;
      location?: { region: string | null; district: string | null; match: string };
      warnings: string[];
    }>("/api/geometry/validate", { method: "POST", body: JSON.stringify({ geometry }) }),
  splitGeometry: (geometry: unknown, line: unknown) =>
    request<{ parts: Array<{ geometry: unknown; area_ha: number }> }>("/api/geometry/split", {
      method: "POST",
      body: JSON.stringify({ geometry, line }),
    }),
  importFarm: async (file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return request<{ farm: Farm; format: string; warnings: string[] }>("/api/farms/import", {
      method: "POST",
      body: form,
    });
  },

  startAnalysis: (farmId: string, body: Record<string, unknown>) =>
    request<{ cached: boolean; job_id: string | null; analysis?: Analysis }>(`/api/farms/${farmId}/analysis`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),
  latestAnalysis: (farmId: string) => request<{ analysis: Analysis | null; job: Partial<Job> | null }>(
    `/api/farms/${farmId}/analysis/latest`,
  ),

  farmLayer: (farmId: string, layerId: string, fieldId?: string | null) =>
    request<LayerChip>(
      `/api/farms/${farmId}/layers/${layerId.replace(/-/g, "_")}${fieldId ? `?field_id=${encodeURIComponent(fieldId)}` : ""}`,
      { method: "GET" },
    ),

  satelliteContext: (farmId: string, bufferM = 2500) =>
    request<SatelliteChip>(`/api/farms/${farmId}/satellite?buffer_m=${bufferM}`, { method: "GET" }),

  addSoilTest: (farmId: string, body: Record<string, unknown>) =>
    request<{ soil_test: SoilTest }>(`/api/farms/${farmId}/soil-tests`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  addRecord: (farmId: string, body: Record<string, unknown>) =>
    request<{ record: Record<string, unknown> }>(`/api/farms/${farmId}/records`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  searchPlaces: (query: string) =>
    request<{ results: PlaceResult[] }>(`/api/places/search?q=${encodeURIComponent(query)}&limit=12`),
  reversePlace: (lat: number, lon: number) =>
    request<{
      inside_ghana: boolean;
      place: { name: string; type: string; distance_km: number } | null;
      region: string | null;
      district: string | null;
      match: string;
    }>(`/api/places/reverse?lat=${lat}&lon=${lon}`),
  regions: () => request<{ regions: string[] }>("/api/geo/regions"),
  districts: (region?: string) =>
    request<{ districts: Array<{ name: string; region?: string }> }>(
      `/api/geo/districts${region ? `?region=${encodeURIComponent(region)}` : ""}`,
    ),

  crops: () =>
    request<{ crops: CropSummary[]; zones: Record<string, unknown>; sources: Record<string, unknown> }>(
      "/api/catalog/crops",
    ),
  providers: () =>
    request<{ verified_on: string; providers: ProviderSpec[] }>("/api/catalog/providers"),
  attribution: () => request<Record<string, unknown>>("/api/sources/attribution"),
  advisories: () => request<{ advisories: Array<Record<string, unknown>> }>("/api/admin/advisories"),
  marketPrices: () => request<{ prices: Array<Record<string, unknown>>; note: string }>("/api/market-prices"),

  exportUrl: (farmId: string, format: "geojson" | "kml" | "csv" | "pdf") =>
    `${BASE}/api/farms/${farmId}/export?format=${format}`,
};

export async function pollJob(
  jobId: string,
  onUpdate: (job: Job) => void,
  options: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Job> {
  const interval = options.intervalMs ?? 900;
  const timeout = options.timeoutMs ?? 6 * 60 * 1000;
  const started = Date.now();
  for (;;) {
    if (options.signal?.aborted) throw new ApiError(499, "Analysis cancelled");
    const job = await api.getJob(jobId);
    onUpdate(job);
    if (job.status === "complete" || job.status === "failed") return job;
    if (Date.now() - started > timeout) {
      throw new ApiError(408, "Analysis is taking longer than expected. You can retry from the panel.");
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-GH", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatShortDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

export function relativeAge(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.round(diffMs / 60000);
  if (Math.abs(minutes) < 2) return "just now";
  if (Math.abs(minutes) < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 36) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} d ago`;
}
