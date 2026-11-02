# Architecture

```
Browser (React 19 + TypeScript + MapLibre GL JS, PWA)
  ├─ self-hosted vector basemap (GeoJSON from the Geofabrik Ghana extract)
  ├─ drawing / editing / GPS / import / split  →  GeoJSON (WGS84)
  ├─ IndexedDB: farms, records, analyses (offline)
  └─ service worker: app shell + basemap/catalogue cache
            │  /api (same origin)
            ▼
FastAPI (Python 3.12)
  ├─ api/core.py      health, metadata, catalogue, places, boundaries, basemap, chips
  ├─ api/farms.py     farm CRUD, fields, geometry validation/split, import, analysis jobs, exports
  ├─ api/layers.py    on-demand PNG overlays for soil, land cover, slope and elevation
  ├─ api/admin.py     advisories + market prices (token-protected when configured)
  ├─ geo/geodesy.py   geodesic area/perimeter/centroid, validation, splitting
  ├─ geo/raster.py    windowed reads, polygon masks, area weights, weighted statistics, chips
  ├─ geo/layer_render.py  polygon-clipped soil/land-cover/terrain overlays + legends
  ├─ geo/admin.py     region/district lookup, agro-ecological zone classifier
  ├─ geo/places.py    local OSM index search + reverse lookup
  ├─ providers/       one typed adapter per dataset + registry + cache/retry harness
  ├─ engine/          crop KB, suitability rules, recommendations, calendar
  ├─ io/              KML/GPX/GeoJSON import; GeoJSON/KML/CSV/PDF export
  └─ analysis.py      orchestration, partial progress, analysis cache, job runner
            │
            ▼
External providers (server-side only, no keys in the browser)
  ISRIC SoilGrids (WebDAV/VRT) · Open-Meteo · CHIRPS (DE Africa COGs)
  Sentinel-2 L2A COGs (AWS, Element84 STAC) · Copernicus DEM · ESA WorldCover
            │
            ▼
SQLite (WAL): farms, fields, records, soil tests, advisories, prices,
              provider cache, analysis cache, jobs, provider health, place index
```

## Why the browser never calls providers directly

- No API keys in client code (even the keyless providers are proxied so caching, retries and field
  masking are consistent).
- All raster work (COG range reads, reprojection, polygon masking, area weighting) runs on the server
  with GDAL/rasterio. The browser only receives compact JSON and small PNG chips.
- A modest Android phone never downloads a satellite scene; it downloads an ~80 KB PNG for its field.

## Farm geometry lifecycle

1. **Client** collects vertices (map taps, GPS walk, import) and shows a live preview area.
2. **Server** (`POST /api/geometry/validate` or `/api/farms`) normalises the geometry with shapely:
   closes rings, removes duplicate vertices, converts GeometryCollection/FeatureCollection to a
   polygon, rejects self-intersections (`explain_validity` is returned to the user), checks area
   bounds (0.005–50,000 ha), and checks the polygon is inside Ghana (bbox plus national outline).
3. **Geodesic measurements** use pyproj `Geod(ellps="WGS84")`:
   `geometry_area_perimeter` gives m² and m on the ellipsoid. The centroid is computed in EPSG:6933
   (equal-area) and transformed back.
4. Stored as WGS84 GeoJSON with 7 decimal places, a stable UUID, timestamps and provenance
   (`drawn`/`imported`/`sample`).
5. Overlap warnings compare the new polygon against all other farms and fields.

## Polygon-based raster sampling (`geo/raster.py`)

For each raster and polygon:

1. Project the polygon into the raster CRS.
2. Compute an integer pixel window covering the polygon bounds (+1 px), clamped for warped VRTs.
3. Read only that window (`boundless=True` for plain COGs).
4. Rasterise the polygon into the window (`all_touched=False`) — the mask is the field, not the bbox.
5. Compute **per-pixel ground area weights**:
   - projected rasters: `|a·e − b·d|` (constant);
   - geographic rasters: geodesic dx·dy per row (latitude-aware), so 0.05° cells are not treated as
     equal rectangles.
6. Valid mask = inside polygon ∧ finite ∧ ≠ nodata ∧ within the property's plausible range.
7. Statistics use weighted mean/median/percentiles/SD; `valid_fraction` is the share of the polygon's
   sampled pixel area that was valid, and `coverage_fraction` compares sampled pixel area with the
   geodesic area.
8. Sub-field honesty: `sub_field_detail = polygon_area / pixel_area ≥ 4`; a farm smaller than one cell
   is reported as describing that cell.

SoilGrids is sampled in its **native equal-area projection** (Interrupted Goode Homolosine), where a
pixel is exactly 62,500 m². Warping to EPSG:4326 first was found to create coverage artefacts.

## Map overlays

`GET /api/farms/{farm_id}/layers/{layer_id}?field_id=` renders one PNG per evidence layer:

| Layer | Source | Native grid | Display |
|---|---|---|---|
| `soil_ph`, `soil_carbon`, `soil_texture` | SoilGrids VRT (native equal-area) | 250 m | nearest-upsampled to 10 m, clipped to boundary |
| `cropland` | ESA WorldCover 2021 | 10 m | clipped to boundary |
| `slope`, `elevation` | Copernicus DEM GLO-30 | 30 m | nearest-upsampled to 10 m, clipped to boundary |
| `evapotranspiration` | WaPOR (unavailable) | — | explicit unavailable response, never fabricated |

`GET /api/farms/{farm_id}/satellite?buffer_m=1500` renders an optional recent-scene true-colour image
around the farm (used for the API and live tests, not the basemap).

The **Satellite toggle** itself is a MapLibre raster source for the EOX **Sentinel-2 cloudless 2025**
WMTS tiles (`https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg`,
CC BY-SA 4.0). It is added below the evidence overlays and farm boundaries, hidden by default, and
shown while the toggle is on; the vector basemap layers are hidden at the same time. The service worker
does not cache these tiles.

PNGs live in the cache directory (`cache/chips/layer_*.png`) and their metadata in `provider_cache`
(180-day TTL). The seed values keep their native resolution: each 250 m or 30 m block stays a block,
only the outer edge follows the field boundary. Legends and value ranges travel with the response,
so the map and the panel cannot drift apart.

## Providers and caching

- `providers/base.py` provides:
  - `request()` — timeout, two retries with exponential backoff, typed `ProviderError`, health recording;
  - `cached()` — SQLite cache with TTL and **stale-while-error** fallback (returns the last payload with
    `stale: true` and its original date);
  - `Section` — status, data, sources, retrieval time, message, warnings;
  - `Source` — dataset, URL, licence, resolution, period, method, limitations.
- TTLs: forecast 30 min, rainfall 12 h, imagery 6 h, soil 30 d (per-field sample reused 1 year),
  analysis 3 h. Soil is a static product, so a cached per-field sample is not labelled stale.
- Every provider request is bounded: raster work only reads the farm window or a few 1° tiles.

## Analysis pipeline

`POST /api/farms/{id}/analysis`:

1. Build the cache key: farm id + geometry + crop + planting date + management level + irrigation +
   current day + analysis version.
2. If a non-expired analysis exists and `force` is false → return it (`cached: true`, ~35 ms).
3. Otherwise create a job row and run on a bounded thread pool:
   - **parallel:** weather, soil, terrain, land cover;
   - **sequential:** rainfall (monthly/daily CHIRPS windows), Sentinel-2 (scene search, SCL cloud
     ranking, band reads, chips), WaPOR probe;
   - crop suitability + calendar + recommendations;
4. Progress and a **compact per-section status summary** are written to the job row; the full payload is
   written once, at completion. (The client currently renders progress and the job message; the section
   summaries are available to the API but not drawn.) A job that runs past `GFI_ANALYSIS_TIMEOUT`
   (default 300 s) fails with `analysis_timeout` rather than holding a worker indefinitely, and an
   analysis above `GFI_MAX_ANALYSIS_HA` (default 20,000 ha) is refused up front — the raster read is
   the expensive part, not storage.
5. The payload is cached in `analysis_cache` and the job stores the final result.

## Data model (SQLite)

| Table | Purpose |
|---|---|
| `farms` | boundary, area/perimeter/centroid, crop, planting date, irrigation, source, demo flag |
| `farm_fields` | sub-polygons with their own crop and planting date |
| `farm_records` | observations, actions, visits, notes (JSON payload) |
| `soil_tests` | measured laboratory values with depth, lab, date and report URL |
| `advisories` | GMet advisories with source URL and validity window |
| `market_prices` | admin-entered prices with observation date and source note |
| `provider_cache` | provider payloads with TTL, created/expiry, hit count |
| `analysis_cache` | complete analysis payloads with TTL |
| `jobs` | status, progress, message, partial and final result |
| `provider_health` | last status, latency, message per provider |
| `place_index` | 9,774 OSM places with region/district for offline search |

Migrations are a forward-only list keyed on `PRAGMA user_version` (`backend/app/db.py`).

## Exports

- **GeoJSON**: farm + fields + records summary + measured soil tests.
- **KML**: one placemark per polygon with a simple style.
- **CSV**: farm attributes, fields, soil tests.
- **PDF**: ReportLab report with summary, top actions (with "Why"), soil table, 7-day forecast, dated
  rainfall totals with baseline, latest scene statistics, crop ranking, full source/licence table and
  the disclaimer. Built-in Latin-1 fonts, so typography is normalised safely.

## Security and privacy

- Provider credentials (none are required today) live only in server environment variables.
- CORS defaults to the dev origin list; same-origin production needs nothing.
- Security headers: `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`,
  `Permissions-Policy` (geolocation only), `Cache-Control: no-store` on `/api`.
- Admin writes require `X-Admin-Token` when `GFI_ADMIN_TOKEN` is set.
- **Accounts (optional).** An administrator (bootstrapped from `GFI_ADMIN_USER` / `GFI_ADMIN_PASSWORD`)
  creates named accounts with passwords and an administrator flag in Settings → Accounts. Passwords are
  PBKDF2-SHA256 with a per-account salt; a successful login sets an HttpOnly `gfi_session` cookie whose
  SHA-256 hash is stored with an expiry. Farms carry `owner_id`: a non-administrator sees and edits only
  their own, an administrator sees all, and an unowned row (created before accounts existed) is
  administrator-only. A farm the caller may not see answers 404, not 403. With no accounts the private API
  is protected by `GFI_ACCESS_TOKEN` if set, and open otherwise; `GET /api/auth/me` reports the posture.
- No trackers, no analytics, no third-party scripts. The map attribution is a DOM control.
- Boundaries and coordinates are private: stored in SQLite/IndexedDB, exported only on request, and never
  sent to a provider (providers receive only a bounding box / centroid grid cell).

## Deployment shape

One FastAPI process serves the static PWA and the API on one port. That keeps cookies, CORS, service
worker scope and HTTPS simple on Railway. SQLite and caches live on a mounted volume (`/data`).
