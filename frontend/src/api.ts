import type {
  AccountUser,
  Analysis,
  AuthMe,
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

/** No request may hang forever: a stalled fetch used to lock the whole UI. */
const DEFAULT_TIMEOUT_MS = 45_000;

const TOKEN_KEY = "gfi.accessToken";

/** The deployment's shared access token, if one is configured. Not per-user auth. */
export function getAccessToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAccessToken(token: string): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the gate will simply ask again */
  }
}

function accessHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { "X-Access-Token": token } : {};
}

function notifyUnauthorized(): void {
  window.dispatchEvent(new CustomEvent("gfi:unauthorized"));
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const external = init.signal ?? undefined;
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, DEFAULT_TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", forwardAbort);
  }

  try {
    let response: Response;
    try {
      response = await fetch(`${BASE}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
          ...accessHeaders(),
          ...(init.headers ?? {}),
        },
      });
    } catch (error) {
      if (external?.aborted) throw new ApiError(499, "Request cancelled", error);
      if (timedOut) {
        throw new ApiError(408, "The server did not answer in time. Check your connection and try again.", error);
      }
      throw new ApiError(0, "Network unavailable. Check your connection.", error);
    }
    if (response.status === 401) notifyUnauthorized();
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
    }    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
    external?.removeEventListener("abort", forwardAbort);
  }
}

export const api = {
  health: () =>
    request<{
      status: string;
      time: { time: string; date: string };
      auth?: { farm_data: string; access_token_required: boolean; admin_token_required: boolean };
    }>("/api/health"),
  meta: () => request<Record<string, unknown>>("/api/meta"),

  // ---- accounts -----------------------------------------------------------
  authMe: (signal?: AbortSignal) => request<AuthMe>("/api/auth/me", { signal }),
  login: (username: string, password: string) =>
    request<{ authenticated: boolean; user: AccountUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ authenticated: boolean }>("/api/auth/logout", { method: "POST" }),
  listUsers: () => request<{ users: AccountUser[] }>("/api/auth/users"),
  createUser: (body: { username: string; password: string; is_admin: boolean }) =>
    request<{ user: AccountUser }>("/api/auth/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id: string, body: Record<string, unknown>) =>
    request<{ user: AccountUser }>(`/api/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteUser: (id: string) => request<void>(`/api/auth/users/${id}`, { method: "DELETE" }),

  listFarms: (signal?: AbortSignal) => request<{ farms: Farm[]; count: number }>("/api/farms", { signal }),
  getFarm: (id: string, signal?: AbortSignal) => request<{ farm: Farm }>(`/api/farms/${id}`, { signal }),
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

  startAnalysis: (farmId: string, body: Record<string, unknown>, signal?: AbortSignal) =>
    request<{ cached: boolean; job_id: string | null; analysis?: Analysis }>(`/api/farms/${farmId}/analysis`, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  getJob: (jobId: string, signal?: AbortSignal) => request<Job>(`/api/jobs/${jobId}`, { signal }),
  latestAnalysis: (farmId: string, signal?: AbortSignal) => request<{ analysis: Analysis | null; job: Partial<Job> | null }>(
    `/api/farms/${farmId}/analysis/latest`,
    { signal },
  ),

  farmLayer: (farmId: string, layerId: string, fieldId?: string | null, signal?: AbortSignal) =>
    request<LayerChip>(
      `/api/farms/${farmId}/layers/${layerId.replace(/-/g, "_")}${fieldId ? `?field_id=${encodeURIComponent(fieldId)}` : ""}`,
      { method: "GET", signal },
    ),

  satelliteContext: (farmId: string, bufferM = 2500, signal?: AbortSignal) =>
    request<SatelliteChip>(`/api/farms/${farmId}/satellite?buffer_m=${bufferM}`, { method: "GET", signal }),

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

  searchPlaces: (query: string, signal?: AbortSignal) =>
    request<{ results: PlaceResult[] }>(`/api/places/search?q=${encodeURIComponent(query)}&limit=12`, { signal }),
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

/**
 * Download an export as a blob rather than navigating to the URL.
 *
 * `window.open` cannot send the access-token header, so a token-protected
 * deployment would answer 401 to every export.
 */
export async function downloadExport(
  farmId: string,
  format: "geojson" | "kml" | "csv" | "pdf",
): Promise<Blob> {
  const controller = new AbortController();
  // PDF generation is slower than a JSON read.
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS * 2);
  try {
    const response = await fetch(`${BASE}/api/farms/${farmId}/export?format=${format}`, {
      headers: accessHeaders(),
      signal: controller.signal,
    });
    if (response.status === 401) notifyUnauthorized();
    if (!response.ok) throw new ApiError(response.status, `Export failed (${response.status})`);
    return await response.blob();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, "The export could not be downloaded. Check your connection.", error);
  } finally {
    clearTimeout(timer);
  }
}

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
    const job = await api.getJob(jobId, options.signal);
    // An abort issued while the request was in flight must not be applied to the
    // panel of whatever farm is selected by now.
    if (options.signal?.aborted) throw new ApiError(499, "Analysis cancelled");
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
