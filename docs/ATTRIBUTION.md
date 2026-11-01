# Data sources and licences

All attribution shown in the app (`Settings → Data sources & licences`, and the map attribution
control) is generated from the typed registry in `backend/app/providers/registry.py`. This page is
the human-readable version.

## Obligations summary

| Dataset | Provider | Licence | Obligation | Commercial use |
|---|---|---|---|---|
| SoilGrids 2.0 | ISRIC — World Soil Information | CC BY 4.0 | Attribute ISRIC; indicate changes | Allowed |
| Open-Meteo forecast / archive | Open-Meteo | CC BY 4.0 (data) | Attribute Open-Meteo | Data: yes. **Hosted free tier: non-commercial** — self-host or license for commercial deployments |
| CHIRPS v2.0 | UCSB Climate Hazards Center, mirrored by Digital Earth Africa | Public domain (CHIRPS); mirror CC BY 4.0 | Cite Funk et al. (2015) | Allowed |
| Sentinel-2 L2A COGs | ESA / Copernicus, hosted on AWS Open Data (`sentinel-cogs`), indexed by Element84 earth-search STAC | Free and open under the Copernicus data policy | "Contains modified Copernicus Sentinel data [year]" | Allowed |
| Copernicus DEM GLO-30 | ESA / DLR / Airbus, AWS Open Data | Free with attribution | © DLR e.V. 2010-2014 and © Airbus DS 2014-2018, provided under COPERNICUS by the EU and ESA | Allowed with attribution |
| ESA WorldCover v200 | ESA WorldCover project | CC BY 4.0 | © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) | Allowed |
| geoBoundaries GHA | geoBoundaries (William & Mary geoLab) | ADM0/ADM2 CC BY 4.0; ADM1 CC BY-SA 2.0 | Attribute; share-alike applies to ADM1 derivatives | Allowed with conditions |
| OpenStreetMap Ghana extract | OpenStreetMap contributors, Geofabrik | ODbL 1.0 | "© OpenStreetMap contributors"; share-alike for derived databases | Allowed with ODbL |
| Mapzen / AWS terrain tiles | Mapzen Terrain Tiles (AWS Open Data) | CC BY 4.0 / public-domain elevation sources | Attribute | Allowed |
| Sentinel-2 cloudless 2025 basemap | EOX IT Services GmbH | CC BY-SA 4.0 | "Sentinel-2 cloudless 2025 by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2025)"; share-alike applies to adapted tiles | Allowed with attribution/share-alike |
| WaPOR v3 | FAO | CC BY-NC-SA 4.0 | Attribute FAO; **non-commercial**; share-alike | **Not allowed without FAO permission** |
| GAEZ v5 | FAO / IIASA | CC BY-NC-SA 3.0 IGO | Attribute; **non-commercial**; share-alike | **Not allowed without permission** |
| GMet advisories | Ghana Meteorological Agency | Government publication, quoted with link | Quote with source link; no bulk copying | Per GMet terms |
| Crop reference database | Compiled from FAO EcoCrop, FAO crop calendar, FAO-56, FAO GIEWS, MoFA, Ghana National Seed Policy, ICCO/COCOBOD, GMet | Each source's own licence | Cite each source (see `backend/data/GHANA_CROPS_SOURCES.md`) | Depends on source |

## Exact attribution text used in the app

- **Map:** `© OpenStreetMap contributors (ODbL) · geoBoundaries · ISRIC SoilGrids (CC BY 4.0)`
- **Soil section:** "ISRIC SoilGrids 2.0 — pH, SOC, nitrogen, texture, CEC, bulk density, coarse fragments … CC BY 4.0 … model prediction at 250 m".
- **Weather section:** "Open-Meteo forecast (best_match global model) … CC BY 4.0 (data); hosted free tier is non-commercial".
- **Rainfall section:** "CHIRPS v2.0 daily and monthly rainfall (Digital Earth Africa mirror) … CHIRPS: public domain (UCSB CHC); DE Africa mirror: CC BY 4.0".
- **Satellite basemap:** "Satellite basemap — Sentinel-2 cloudless 2025 · 10 m · © EOX (CC BY-SA 4.0)" and the full EOX attribution in Settings → Data sources & licences.
- **Vegetation section:** "Copernicus Sentinel-2 L2A (10 m/20 m) — AWS Open Data COGs via Element84 STAC … Copernicus Sentinel data — free and open (contains modified Copernicus Sentinel data)".
- **Terrain section:** "Copernicus DEM GLO-30 … © DLR e.V. 2010-2014 and © Airbus DS 2014-2018…".
- **Land cover section:** "ESA WorldCover 2021 v200 (10 m) … CC BY 4.0 — © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021)".
- **Water section:** "FAO WaPOR v3 … CC BY-NC-SA 4.0" (shown with the unavailable state).

## Licence notes specific to this build

- **No OSM tiles are downloaded, prefetched or packaged.** The basemap is generated from the Geofabrik
  `.osm.pbf` extract by `scripts/build_ghana_basemap.py`; the tile policy of `tile.openstreetmap.org`
  is not engaged at all.
- The **SoilGrids VRT metadata** bundled in `backend/data/seed/soilgrids_vrt/` contains only ISRIC's
  mosaic index (rewritten to absolute HTTPS sources), not SoilGrids raster values. Farm analyses read
  individual ISRIC tiles at request time.
- The **CHIRPS 1991–2020 monthly climatology** in `backend/data/seed/chirps/` is a derived Ghana
  subset of a public-domain product, created by `scripts/seed_chirps_climatology.py`.
- The **geoBoundaries ADM1 layer carries CC BY-SA 2.0** (share-alike). If you redistribute a modified
  ADM1 layer, keep the same licence. ADM2 and ADM0 are CC BY 4.0.
- **WaPOR and GAEZ are non-commercial.** They are not queried in this release (WaPOR API returned
  HTTP 502 at verification; GAEZ has no polygon machine API), so no non-commercial data is included
  in the current dataset. Any future connection must respect those licences.
- Open-Meteo's hosted API is free for **non-commercial** use. A commercial deployment must self-host
  Open-Meteo or buy a commercial plan; set `GFI_*` provider overrides (see `docs/LIMITATIONS.md`).

## Application code

MIT — see [`../LICENSE`](../LICENSE). Third-party JavaScript and Python dependencies keep their own
licences as declared in `frontend/package.json` and `backend/requirements.txt`.
