# Measured performance

Measured on 31 October 2026 from the authoring machine:

- Windows, Node 24.19, Python 3.12.14, Chromium (Playwright headless, software rendering).
- Backend served by Uvicorn on `127.0.0.1:8000`, frontend served from the same origin.
- Raw results: [`performance.json`](performance.json). Re-run with
  `cd frontend && node tests/e2e/perf.mjs` (the app must be running).

## Load and transfer (cold cache, one visit)

| Metric | Desktop (1440×900) | Mobile viewport (390×844) |
|---|---:|---:|
| DOM content loaded | 236 ms | — |
| Map canvas present | **591 ms** | **600 ms** |
| Total transfer | **765–813 KB** across runs | — |
| JavaScript (gzip) | 381 KB | — |
| CSS (gzip) | 19 KB | — |
| Fonts | Inter variable WOFF2, subset by the font package | — |

The transfer figure includes the self-hosted basemap layers, boundary GeoJSON, catalogue and the first
analysis for the sample farm; repeated runs measured 765–813 KB. Gzip is applied by
`GZipMiddleware` (the seed `.gz` files set their own `Content-Encoding` and are skipped).
Service-worker precache then serves the shell offline.

Bundle composition: MapLibre GL JS dominates the 1.4 MB uncompressed JS (381 KB gzip). It is not
code-split yet because the map is required for first paint; this is a candidate optimisation for very
slow connections.

## Analysis latency (31.7 ha polygon, maize)

| Case | Wall time | Notes |
|---|---:|---|
| **Cold, first ever run** (empty caches, downloads SoilGrids/CHIRPS/imagery) | **38.1 s** | measured 31 Oct 2026, cold process |
| **Cold, with provider caches warm** | **18.5 s** | soil + rainfall month/day caches present |
| **Repeat within 3 h** (`cached: true`) | **36 ms** | analysis cache |
| Server-reported elapsed | 18.2 s | inside the response payload |
| Final payload | **94.7 KB** | all sections + provenance |
| Section statuses | 9 ok, 1 unavailable (WaPOR) | weather, soil, terrain, land cover, rainfall, vegetation, crops, calendar, recommendations |

Partial results are streamed into the job row, so weather/soil/terrain tabs appear before the
Sentinel-2 section finishes.

### Where the cold time goes

- SoilGrids: 33 layers (9 properties × 3 depths + 6 pH quantiles). The first read of each VRT tile
  costs HTTP range reads; results are cached per field for a year.
- CHIRPS: 30 daily COGs for the recent series plus monthly COGs for the 30/90-day windows and the
  13-month series.
- Sentinel-2: search, SCL cloud ranking for up to 8 candidate scenes, then band windows for the
  latest usable scene (+ trend scenes) and three PNG chips.

## Frame rate

| Measurement | Result |
|---|---|
| Idle rAF on mobile viewport (headless) | ~60 fps |
| **During synthetic drag gestures, mobile viewport, headless software rendering** | **~15 fps** (median frame 66.8 ms, p95 83.4 ms) |

**This is not a claim about real phones.** Playwright's headless Chromium on this machine renders with
software GL (SwiftShader) at 2× device pixel ratio; the 15 fps figure is a lower bound from a
non-GPU environment, not a budget-Android measurement.

Optimisations applied because of these measurements:

- roads split into major (from z6) and minor (from z8.5) layers, halving rendered features at
  country zoom;
- district boundaries hidden below z7.5;
- labels re-rendered only when the view signature changes, debounced (150–260 ms);
- map labels as a small number of DOM markers (no glyph PBFs, no symbol layers);
- NDVI/NDMI overlays rendered with nearest-neighbour resampling (honest 10 m cells, cheaper than
  smoothing);
- service-worker caching of basemap layers, so repeat visits do not re-download 0.8 MB.

A real measurement on the target device is still needed before claiming comfort on low-end hardware.
Record it with the device name, network type and the same `perf.mjs` script.

## Map layers

Rendered on first toggle and then cached for 180 days on the volume:

| Layer | First render (cold) | Cached | Output |
|---|---:|---:|---|
| Soil pH / carbon / clay (250 m) | 1.8 s | 0.1 s | 150 × 150 PNG, ~5–20 KB |
| Land cover (10 m) | 0.4 s | 0.1 s | 66 × 64 PNG, ~1 KB |
| Slope / elevation (30 m) | 0.9 s | 0.1 s | 60 × 57 PNG, ~2 KB |

The first soil render includes the VRT range read; the terrain render reuses the DEM tile mosaic.
Layers are fetched only when a farmer turns them on, so they add nothing to first load.

## Satellite basemap

The toggle streams **256 px EOX Sentinel-2 cloudless 2025** tiles only while it is active; nothing is
fetched on first load. The layer requests z14 tiles and is overzoomed beyond that.

| Metric | Value |
|---|---:|
| Tile size | 8–25 KB JPEG |
| Tiles per viewport at z14 | ~12–20 |
| Transfer when first enabled | ~150–400 KB |
| Server work | none (tiles come from EOX) |
| Repeat pan within the session | browser HTTP cache |

The vector basemap, roads and water layers are hidden while the imagery is shown; farm boundaries and
evidence overlays stay above it. The service worker does not cache these tiles, so offline use falls
back to the vector map.

## Caching behaviour observed

- Provider cache rows store a hit counter; repeat analyses inside the TTL are served entirely from
  cache.
- Soil per-field samples are valid for one year; soil sections are not marked stale merely because
  they were cached.
- A failed refresh returns the last successful payload marked `stale` with its original retrieval
  date (covered by the unit test `test_cache_returns_stale_payload_when_refresh_fails`).
