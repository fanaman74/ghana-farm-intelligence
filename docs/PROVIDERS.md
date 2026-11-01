# Provider verification and integration report

Verified **31 October 2026** from a Windows workstation with Python 3.12, rasterio 1.5.2 (GDAL 3.12.2),
shapely 2.2.0, pyproj 3.8.0. The app records the same registry at runtime
(`GET /api/catalog/providers`) including the last live health check.

Two farm polygons were used for smoke tests:

- **South:** Ejisu area, Ashanti — \(6.6974°N, 1.4542°W\) (deciduous/transition, bimodal).
- **North:** Bolgatanga area, Upper East — \(10.8185°N, 0.9240°W\) (Guinea/Sudan savannah, unimodal).

| # | Provider | Endpoint used | Coverage | Resolution | Licence | Key | Smoke test | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | **ISRIC SoilGrids 2.0** | `https://files.isric.org/soilgrids/latest/data/<prop>/<prop>_<depth>_<stat>.vrt` via GDAL `/vsicurl` | Global, Ghana covered | 250 m | CC BY 4.0 | none | Read `phh2o_0-5cm_mean` at both farms: pH 5.5–6.2, 0–100 % valid cells; source tiles 600×600 px | **Live** |
| 2 | **Open-Meteo forecast** | `https://api.open-meteo.com/v1/forecast` | Global | ~11 km `best_match` | CC BY 4.0; free tier non-commercial | none | 200 response, 21 daily rows (5 past + 16 forecast), 48 hourly rows, ET₀ and soil moisture present | **Live** |
| 3 | **Open-Meteo archive (ERA5)** | `https://archive-api.open-meteo.com/v1/archive` | Global | ~9–25 km | CC BY 4.0 | none | 2015–2019 daily temperature normals returned and cached | **Live** (temperature normals only) |
| 4 | **CHIRPS v2.0** | `https://deafrica-input-datasets.s3.af-south-1.amazonaws.com/rainfall_chirps_daily/…` and `rainfall_chirps_monthly/…` (COG range reads) | 50°S–50°N | 0.05° (~5.5 km) | Public domain (UCSB CHC); mirror CC BY 4.0 | none | Daily COGs available to **2026-09-30**; monthly from 1981; 30-day total 107 mm at the south farm | **Live** |
| 5 | **CHIRPS climatology** | Bundled derivation of the same monthly product | Ghana | 0.05° | Public domain | none | `seed_chirps_climatology.py`: 360/360 monthly files, 12 bands, 70 KB | **Live** (derived) |
| 6 | **Sentinel-2 L2A** | `https://earth-search.aws.element84.com/v1/search` → `https://sentinel-cogs.s3.us-west-2.amazonaws.com/...` COGs | Global | 10 m / 20 m | Copernicus free and open | none | Scene `S2A_30PYS_20261029`; 100 % cloud-free over the north farm; NDVI 0.31, NDMI −0.18 from masked pixels | **Live** |
| 7 | **Copernicus DEM GLO-30** | `https://copernicus-dem-30m.s3.amazonaws.com/...` COG | Global | 30 m | Free with attribution | none | Elevation 251.8–284.1 m, mean slope 1.7 %; 360 valid pixels | **Live** |
| 8 | **ESA WorldCover 2021** | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/...` COG | Global | 10 m | CC BY 4.0 | none | Lake Bosumtwi read returned 100 % permanent water (alignment check); cropland 58 % near Tamale | **Live** (2021 baseline) |
| 9 | **geoBoundaries GHA** | `https://www.geoboundaries.org/api/current/gbOpen/GHA/ADM{0,1,2}/` | Ghana | Vector | ADM0/ADM2 CC BY 4.0; ADM1 CC BY-SA 2.0 | none | 1 outline, 16 regions, 260 districts downloaded and simplified | **Live** (bundled) |
| 10 | **OpenStreetMap (Geofabrik)** | `https://download.geofabrik.de/africa/ghana-latest.osm.pbf` (117 MB) | Ghana | Vector | ODbL 1.0 | none | 9,774 places, 4,063 road features, 728 water features extracted with pyosmium | **Live** (bundled) |
| 11 | **Mapzen/AWS terrain tiles** | `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` | Global | ~30–90 m | CC BY 4.0 / public-domain sources | none | Tile fetch 200, ~120 KB per z8 tile; browser-only, 3D off by default | **Live** (optional) |
| 12 | **FAO WaPOR v3** | `https://io.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR` | Africa/Near East | 100 m/300 m | CC BY-NC-SA 4.0 | none | **HTTP 502 on every documented path** on repeated attempts; DE Africa `wapor-v3/` mirror holds only STAC metadata, no rasters | **Unavailable** |
| 13 | **FAO/IIASA GAEZ v5** | `https://gaez.fao.org` | Global | ~10 km | CC BY-NC-SA 3.0 IGO | none | Interactive download workflow only; no documented machine endpoint for polygon subsets | **Not connected** |
| 14 | **GMet advisories** | `https://www.meteo.gov.gh/Services/agrometeorology/` and seasonal forecast pages | Ghana | Regional | Government publication | none | Pages reachable; no machine API confirmed, so an admin source-linked workflow is implemented instead of scraping | **Manual ingest** |
| 15 | **Market prices** | — | — | — | — | — | No free, reliable, licensed price API verified. Admin-entered records only, with observation date and source note | **Manual ingest** |

## Important access decisions (and why)

### SoilGrids: VRT, not REST, not WCS

The build prompt states the SoilGrids REST API is paused; it was not used. Two other paths were tested:

1. **WCS 2.0.1 `GetCoverage`** with `SUBSETTINGCRS`/`OUTPUTCRS` in EPSG:4326. Small subsets returned
   **periodic zero artefacts** (a 0.2° window around Kumasi contained 43 % zeros; a 0.05° window 97 %
   zeros, even though the same pixels are valid in the source tiles). Values were plausible only at
   country scale, so WCS was **rejected** — it would have produced false low values inside fields.
2. **WebDAV/VRT** (ISRIC's documented bulk route). Each VRT is a ~3.5 MB mosaic index of 7,692
   600×600 tiles; gzipped it is ~100–160 KB. The app bundles rewritten VRT metadata (4.5 MB total) and
   reads only the tiles covering the farm.
3. **Sampling projection.** Sampling is done in the VRT's native Interrupted Goode Homolosine grid
   (equal-area, constant 62,500 m² pixels). Warping to EPSG:4326 before sampling produced coverage
   artefacts in testing, so it is avoided.

### CHIRPS recency

The Digital Earth Africa mirror published daily files through **2026-09-30** at verification, i.e. a
31-day lag behind the calendar. The UI always shows the last available date and computes totals to
that date; it never relabels them as "today".

### Sentinel-2 discovery

`explorer.digitalearth.africa/stac` returned **HTTP 200 with a server-error HTML page** during
verification, so DE Africa STAC search was unusable. Element84's earth-search v1 indexes the same
Copernicus L2A products hosted on AWS Open Data and returned valid items. The app records this
substitution in its provenance text.

### Satellite basemap tiles

The map's Satellite toggle uses the **EOX Sentinel-2 cloudless 2025** WMTS tiles
(`https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg`, 256 px JPEG,
CC BY-SA 4.0). Tiles are requested only while the toggle is on; z10 and z14 tiles for the Bolgatanga
sample returned HTTP 200, image/jpeg, 8–24 KB. The annual mosaic is not current imagery and is soft at
field zoom, so the toggle opens at a regional zoom. Attribution is displayed in the map chip and in
Settings → Data sources & licences.

### WaPOR

At verification the FAO API host returned HTTP 502 for every path (catalogue, cubes, WCS). The
adapter probes and reports `unavailable`; no evapotranspiration number is fabricated from another
source. Water guidance therefore uses CHIRPS rainfall and Open-Meteo ET₀ only, and says so.

## Runtime health behaviour

- Every provider call has a timeout (45 s default), two retries with backoff, and a SQLite cache with
  a per-source TTL (weather 30 min, rainfall 12 h, imagery 6 h, soil 30 days, analysis 3 h).
- A failed refresh falls back to the last successful payload and marks the section `stale` with its
  original retrieval date.
- Provider health (status, latency, timestamp) is recorded on each call and exposed at
  `GET /api/catalog/providers`.
- Soil is a static product, so a per-field cached sample is reused for a year and is **not** labelled
  stale; only a provider failure is.

## Reproducing the verification

```bash
cd backend
python scripts/seed_chirps_climatology.py      # 360 monthly COGs -> Ghana 12-band climatology
python scripts/build_ghana_admin.py            # geoBoundaries ADM0/1/2 -> seeds
python scripts/seed_soilgrids.py               # VRT metadata -> gzipped seed
python scripts/verify_providers.py             # live smoke test (network), writes JSON
```

`scripts/verify_providers.py` performs a live check of each endpoint and reports HTTP status, latency
and a sample value where applicable. It is intentionally separate from the deterministic unit tests.
