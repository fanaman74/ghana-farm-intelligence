# Known limitations

This page is deliberately blunt. The app is a screening and planning aid; it is not a laboratory,
a licensed agronomist, a cadastre or a yield model.

## Data that is unavailable in this release

| Item | State | What the app does |
|---|---|---|
| **FAO WaPOR v3** (AETI, net primary production, WaPOR soil moisture) | Provider returned **HTTP 502** on every documented path on 31 Oct 2026. The Digital Earth Africa `wapor-v3/` mirror contains only STAC metadata, no rasters | Shows an explicit unavailable card; water advice uses CHIRPS rainfall and Open-Meteo ET₀ only and says so |
| **FAO/IIASA GAEZ v5** | No documented machine API for polygon subsets; only interactive bulk download | Crop ranking uses a documented local rule engine (published crop ranges + SoilGrids + CHIRPS climatology + ERA5 normals + terrain). It is labelled **rule-based screening, not a GAEZ query** |
| **GMet machine-readable advisories** | No supported API found; pages are human-readable | Administrator enters advisories with title, body, source URL, validity window and region. Absence of an advisory is **not** evidence that conditions are normal |
| **Market prices / pest alerts** | No free, reliable, licensed price feed verified | Administrator-entered records with observation date and source note; no economic suitability score is produced |
| **WorldCereal** | DE Africa STAC search returned a server-error page | Not shown; WorldCover 2021 is shown as a historical baseline instead |
| **TAMSAT cross-check** | Registration required | Not integrated |

## Measurement and resolution honesty

- **Soil** is a 250 m prediction, not a sample. A 25 ha field is about 4 grid cells. The app says when the
  field is smaller than one cell and suppresses any suggestion of within-field soil detail. Some cells
  have **no prediction** (for example over built-up land around Kumasi); the app reports "no prediction"
  rather than borrowing a neighbouring value. The soil map layers are drawn from the same cells: a
  small field shows a few large blocks, and the edge is clipped to the boundary only for legibility —
  it does not add precision.
- **Rainfall** is a satellite/station blend at ~5.5 km, not a gauge on the field. CHIRPS publication lags
  the calendar: at verification the newest day was **2026-09-30**, 31 days behind. Totals always end on
  the newest published day and say so.
- **Weather** is a ~11 km model grid cell. Its coordinates and distance from the farm centroid are shown.
  Tropical convective rainfall is localised; a forecast value is not a field measurement.
- **Sentinel-2** is 10 m (red/NIR) and 20 m (SWIR/SCL). A nominal 5-day revisit is not a guarantee of a
  cloud-free image. The app ranks scenes by the **cloud-free fraction over the field**, masks clouds and
  shadows with the SCL band, and shows the valid fraction. NDVI/NDMI describe relative canopy condition —
  they cannot identify pests, diseases or nutrient deficiencies.
- The **Satellite button** switches the whole map to the **Sentinel-2 cloudless 2025** mosaic by EOX
  (10 m, CC BY-SA 4.0). It is an annual composite, not today's imagery; at field zoom it is upscaled and
  soft, so the app opens it at a regional zoom. The latest individual scene remains in the Sentinel-2
  tab. The tile service is external: the toggle needs a connection and refuses to switch when offline.
- **Terrain** is a 30 m DEM. It cannot resolve field drainage or waterlogging and is not an engineering
  flood model.
- **Land cover** is the 2021 WorldCover baseline, never "today's crop".
- **Agro-ecological zone** is approximated from rainfall, latitude and coast distance using MoFA's zone
  descriptions. Real zone boundaries are gradual.

## Interpretive limits

- No **yields**, **prices**, **fertiliser rates**, **lime rates** or **pesticide dosages** are produced.
  Acidity advice always says to confirm with a soil test before treating.
- No **land ownership** or cadastral inference. Boundaries are private, stored locally, and never published.
- Crop suitability must not be read as an investment recommendation. Perennials (cocoa, oil palm,
  cashew, plantain) are assessed as establishment/long-term suitability, not as one-season planting choices.
- Recommendations are rules with explicit evidence; they are not generated prose. A "possible water
  stress" statement is a planning signal, not a measured deficit.

## Operational limits

- **SQLite and in-process jobs** mean the deployment should run **one replica**. Horizontal scaling requires
  moving the database to Postgres and jobs to an external queue.
- **Accounts are optional and administrator-created.** Set `GFI_ADMIN_USER` and `GFI_ADMIN_PASSWORD` once to
  create the first administrator; that account then creates every other account in Settings → Accounts
  and decides who else is an administrator. Each account sees only the farms it created; administrators
  see all farms. There is no self-signup, no email, no password-reset-by-email, and no per-farm sharing
  between two non-administrator accounts yet. With no accounts and no `GFI_ACCESS_TOKEN`, every boundary
  is readable and deletable by any client that can reach the API.
- The **administrator API is unauthenticated when `GFI_ADMIN_TOKEN` is empty**. Set the token for any
  public deployment.
- The container **runs the app as an unprivileged user** (uid 10001, `gfi`). Because platform volumes
  usually arrive root-owned, `docker/entrypoint.sh` fixes ownership of `GFI_DATA_DIR` while it is still
  root and then drops privileges; if the volume cannot be chowned the entrypoint says so and the app
  will fail to write. Override the uid/gid with `GFI_APP_UID` / `GFI_APP_GID` if the volume is mounted
  for another owner.
- Provider rate limits apply (see each licence). Caching is per deployment; there is no shared CDN.
- **A partial provider record is labelled, not hidden.** If a CHIRPS raster in a 7/30/90-day window is
  missing, the window reports `complete: false` with its covered and expected day counts, the anomaly
  is compared against the same number of baseline days, and the label says the record is partial. A
  soil or rainfall value is never borrowed from a neighbouring cell.
- **A very large boundary is refused rather than analysed.** Storage accepts up to 50,000 ha, but an
  analysis above `GFI_MAX_ANALYSIS_HA` (default 20,000 ha, about 2 M pixels per Sentinel-2 band) fails
  with `area_too_large_for_analysis` and an explanation, instead of exhausting memory and timing out.
  Split large boundaries into fields.
- The sample farm is a real location analysed with real data, but it is **not a real farm**; it is labelled
  as a sample.
- The **interface is English**. Twi, Ewe and Dagbani are selectable in settings but intentionally fall back
  to English until translations are reviewed by speakers and agronomists; unreviewed agronomic translations
  are not shipped as authoritative.
- Automated tests run against a desktop Chromium and a 390 × 844 viewport. They do not replace testing on
  physical low-end Android devices or over a 2G/3G connection.
- The provided **Docker image was not built in the authoring environment** (no Docker daemon available).
  The Dockerfile and Compose file are written and reviewed against the same steps that were executed
  manually on Windows (Node build + Python install + single-origin serve). Build it once in CI before a
  production launch.

## Things a reviewer should check before using this for real decisions

1. Run `python backend/scripts/verify_providers.py` from the deployment network.
2. Confirm the SoilGrids VRT seed is present (`backend/data/seed/soilgrids_vrt/*.vrt.gz`).
3. Enter at least one real GMet advisory and one real soil test for the target farm.
4. Compare three fields where a laboratory soil test exists with the modelled values, and record the
   differences with the farm records feature.
5. Confirm the daylight theme and touch targets on the actual phones in use.
