"""Typed provider registry — the single source of truth for every data source.

Each entry records: what the source provides, its spatial/temporal resolution,
Ghana coverage, licence, access method, whether a key is needed, the live
integration status at verification time, and the limitations shown to farmers.
Statuses were verified on 2026-10-31 (see docs/PROVIDERS.md).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

VERIFIED_ON = "2026-10-31"

STATUS_CONNECTED = "connected"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"
STATUS_MANUAL = "manual_ingest"
STATUS_PLANNED = "planned"


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    category: str
    variables: str
    resolution: str
    temporal: str
    coverage: str
    licence: str
    licence_notes: str
    access: str
    key_required: bool
    status: str
    homepage: str
    integration_notes: str
    limitations: str
    verification: str
    commercial_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verified_on"] = VERIFIED_ON
        return data


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="soilgrids",
        name="ISRIC SoilGrids 2.0",
        category="soil",
        variables="pH, organic carbon, total nitrogen, sand/silt/clay, bulk density, CEC, coarse fragments; uncertainty quantiles",
        resolution="250 m",
        temporal="Static v2.0 (2017–2022 training data)",
        coverage="Global, includes all of Ghana",
        licence="CC BY 4.0 (SoilGrids 2.0)",
        licence_notes="Attribution to ISRIC — World Soil Information required. Estimates are model predictions, not laboratory measurements.",
        access="OGC WCS 2.0.1 GetCoverage subsets (primary). WebDAV VRT rasters are the documented fallback; the SoilGrids REST API is not used.",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://soilgrids.org",
        integration_notes="WCS GetCoverage with SUBSETTINGCRS=EPSG:4326 and OUTPUTCRS=EPSG:4326 returns GeoTIFF tiles of a padded farm bounding box. Values are stored as scaled integers and are rescaled here. Uncertainty layers (Q0.05/Q0.95) are fetched for pH.",
        limitations="250 m predictions cannot show within-field variation for smallholder plots. Use a laboratory soil test before lime or fertiliser prescriptions.",
        verification="GetCoverage for phh2o_0-5cm_mean over Kumasi and Bolgatanga bboxes returned integer rasters (pH 4.7–6.8) at 0.0021°.",
    ),
    ProviderSpec(
        id="openmeteo_forecast",
        name="Open-Meteo forecast",
        category="weather",
        variables="Rainfall, rain probability, temperature max/min, relative humidity, wind, reference evapotranspiration (ET₀), soil moisture and soil temperature",
        resolution="~11 km model grid (best_match); 9 km ECMWF IFS subset varies",
        temporal="Hourly to 48 h, daily to 16 days",
        coverage="Global, includes all of Ghana",
        licence="CC BY 4.0 (data); hosted free tier is for non-commercial use",
        licence_notes="Open-Meteo's hosted API free tier is non-commercial and rate limited. A commercial deployment must self-host or license a commercial plan.",
        access="HTTPS JSON API, no key required for the free tier",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://open-meteo.com",
        integration_notes="Daily 16-day and hourly 48-hour windows plus current conditions, requested with timezone=Africa/Accra. Grid latitude/longitude returned by the API are stored and shown so the model cell is explicit.",
        limitations="Model output for a grid cell, not an on-farm observation. Tropical convective rainfall is often localised; do not treat a cell value as measured rainfall on the field.",
        verification="Live 200 response for Kumasi with 16 daily rows and 48 hourly rows, ET₀ and soil moisture present.",
        commercial_note="Self-host Open-Meteo or subscribe for commercial use.",
    ),
    ProviderSpec(
        id="chirps",
        name="CHIRPS v2.0 daily/monthly rainfall (Digital Earth Africa mirror)",
        category="rainfall",
        variables="Daily and monthly rainfall totals; 30/90-day totals; anomaly against 1991–2020 climatology",
        resolution="0.05° (~5.5 km)",
        temporal="Daily since 1981; monthly since 1981. Ingestion lag observed at verification: latest complete day 2026-09-30",
        coverage="50°S–50°N, includes all of Ghana",
        licence="CHIRPS: public domain (UCSB Climate Hazards Center). DE Africa mirror: CC BY 4.0",
        licence_notes="Cite Funk et al. (2015), CHIRPS v2.0. Mirror hosted by Digital Earth Africa on AWS Open Data.",
        access="Public S3 HTTPS COGs read with GDAL range requests (/vsicurl), plus a bundled Ghana climatology derived from the same product",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://www.chc.ucsb.edu/data/chirps",
        integration_notes="Monthly COGs provide 30/90-day totals and partial-month pro-rating; daily COGs provide the recent daily series and dry-spell counts. Availability is probed from S3 listings and cached for 6 hours.",
        limitations="A 5.5 km estimate blended from stations and satellites, not a rain gauge on the farm. Recent weeks lag the calendar by up to ~5 weeks in the DE Africa mirror; the UI always shows the last available date.",
        verification="S3 listing found daily COGs through 2026-09-30 and monthly COGs since 1981; window read returned float32 mm values at 0.05°.",
    ),
    ProviderSpec(
        id="sentinel2",
        name="Copernicus Sentinel-2 L2A (AWS Open Data COGs, STAC discovery)",
        category="vegetation",
        variables="NDVI, NDMI, true colour, scene classification (cloud/shadow mask), acquisition date and cloud-free fraction",
        resolution="10 m (B02/B03/B04/B08), 20 m (B11/B12, SCL)",
        temporal="5-day revisit at the equator (two satellites); actual usable cloud-free scenes vary",
        coverage="Global, includes all of Ghana",
        licence="Copernicus Sentinel data — free and open under the Copernicus data policy",
        licence_notes="Contains modified Copernicus Sentinel data. Processed by ESA; COGs hosted by AWS Open Data (sentinel-cogs).",
        access="STAC search (Element84 earth-search v1) then HTTP range reads of band COGs with GDAL",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://registry.opendata.aws/sentinel-2-l2a-cogs/",
        integration_notes="Scenes intersecting the polygon are filtered by scene cloud cover, then ranked by the actual cloud-free fraction over the farm from the 20 m SCL band. Statistics use only valid pixels inside the polygon.",
        limitations="A nominal revisit interval is not a guarantee of a cloud-free image. Vegetation indices show relative condition; they do not identify pests, diseases or nutrient deficiencies.",
        verification="STAC search for Kumasi returned S2B_30NXN_20261022 with 17% scene cloud; polygon window read of B04/B08/SCL succeeded and produced a cloud-masked NDVI.",
    ),
    ProviderSpec(
        id="copernicus_dem",
        name="Copernicus DEM GLO-30",
        category="terrain",
        variables="Elevation, slope, aspect; erosion screening",
        resolution="30 m",
        temporal="Static (2011–2015 acquisition, 2021 release)",
        coverage="Global, includes all of Ghana",
        licence="Copernicus DEM — free, attribution to ESA/DLR/Airbus required",
        licence_notes="© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA.",
        access="AWS Open Data COGs with GDAL range reads; Mapzen/AWS terrain tiles for optional 3D rendering",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://registry.opendata.aws/copernicus-dem/",
        integration_notes="A 1°×1° tile window is read at native 30 m, then slope is derived with geodesic pixel spacing at the farm latitude.",
        limitations="A screening aid, not a cadastral survey or an engineering-grade flood model. 30 m cannot resolve field drainage.",
        verification="N06W002 DEM tile window over the Kumasi test farm returned elevations 248–290 m and read in under 1 s.",
    ),
    ProviderSpec(
        id="esa_worldcover",
        name="ESA WorldCover 2021 v200",
        category="landcover",
        variables="Land-cover class fractions (tree cover, shrub, grass, cropland, built-up, bare, water, wetland)",
        resolution="10 m",
        temporal="2021 only — historical baseline",
        coverage="Global, includes all of Ghana",
        licence="CC BY 4.0 (ESA WorldCover)",
        licence_notes="Attribution: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021).",
        access="AWS Open Data COGs (3°×3° tiles) with GDAL range reads",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://esa-worldcover.org",
        integration_notes="Class fractions are area-weighted inside the polygon. This is a 2021 baseline, never presented as current crop identification.",
        limitations="A 2021 historical baseline: the field may have changed since. Not a crop-type map and not evidence of what is planted now.",
        verification="Tile ESA_WorldCover_10m_2021_v200_N06W003_Map.tif window read returned class codes 10/30/50/60 for the Kumasi test farm.",
    ),
    ProviderSpec(
        id="geoboundaries",
        name="geoBoundaries Ghana ADM0/ADM1/ADM2",
        category="boundaries",
        variables="National outline, 16 regions, 260 districts",
        resolution="Vector (generalised)",
        temporal="gbOpen snapshot (2022)",
        coverage="Ghana",
        licence="ADM2: CC BY 4.0; ADM1: CC BY-SA 2.0; ADM0: CC BY 4.0",
        licence_notes="Licence differs by level; exact terms shown in the in-app attribution page. Share-alike applies to ADM1 derivatives.",
        access="Bundled GeoJSON seed built by scripts/build_ghana_admin.py from geoBoundaries",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://www.geoboundaries.org",
        integration_notes="District polygons provide the administrative label and the region/district picker; they also power the district boundary map layer.",
        limitations="Generalised boundaries; not authoritative for legal or cadastral purposes.",
        verification="ADM1 returned 16 units, ADM2 returned 260 units; simplified GeoJSON downloaded (3.0 MB).",
    ),
    ProviderSpec(
        id="osm",
        name="OpenStreetMap Ghana (Geofabrik extract)",
        category="basemap",
        variables="Roads, localities, water, district labels for the self-hosted vector basemap and local place search",
        resolution="Vector",
        temporal="Geofabrik daily extract",
        coverage="Ghana",
        licence="ODbL 1.0 (OpenStreetMap contributors)",
        licence_notes="Attribution © OpenStreetMap contributors required; share-alike applies to derived databases. No tiles are downloaded from tile.openstreetmap.org.",
        access="One-time extract processed into bundled GeoJSON and a local SQLite locality index",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://download.geofabrik.de/africa/ghana.html",
        integration_notes="Places, roads and water are extracted with pyosmium and served from the app; place search runs entirely locally with SQLite.",
        limitations="Crowd-sourced coverage is uneven in rural areas; some hamlets are missing or misnamed.",
        verification="ghana-latest.osm.pbf (117 MB) downloaded and processed by scripts/build_ghana_basemap.py.",
    ),
    ProviderSpec(
        id="eox_s2cloudless",
        name="Sentinel-2 cloudless 2025 basemap tiles (EOX)",
        category="basemap",
        variables="True-colour cloudless satellite basemap for the whole map",
        resolution="10 m annual mosaic; 256 px WMTS tiles, served to zoom 14 and overzoomed beyond",
        temporal="Annual cloudless composite (2025)",
        coverage="Global, includes all of Ghana",
        licence="CC BY-SA 4.0",
        licence_notes=(
            "Sentinel-2 cloudless 2025 by EOX IT Services GmbH "
            "(Contains modified Copernicus Sentinel data 2025). Share-alike applies to adapted tiles."
        ),
        access="HTTPS WMTS tiles (z/y/x JPEG), no key",
        key_required=False,
        status=STATUS_CONNECTED,
        homepage="https://s2maps.eu",
        integration_notes=(
            "MapLibre raster source behind the Satellite toggle. Tiles are requested only while the "
            "toggle is on and are deliberately not cached by the service worker."
        ),
        limitations=(
            "A 2025 annual composite, not today's imagery. At field zoom it is upscaled and soft; "
            "the Sentinel-2 tab carries the latest scene. Requires a connection."
        ),
        verification="Fetched z10 and z14 tiles for the Bolgatanga sample (HTTP 200, image/jpeg, 8–24 KB).",
    ),
    ProviderSpec(
        id="gmet",
        name="Ghana Meteorological Agency (GMet) official advisories",
        category="advisory",
        variables="Seasonal forecast summaries, onset/cessation, dry-spell and dekadal agrometeorological advice",
        resolution="Regional / national",
        temporal="Per GMet publication",
        coverage="Ghana (regional bulletins)",
        licence="Government of Ghana publication — quoted with source link, not bulk copied",
        licence_notes="No open machine API was found. Bulletins are entered by an administrator with a source URL and validity window, and attributed to GMet.",
        access="Admin-managed ingestion workflow with source links (no scraping, no invented API)",
        key_required=False,
        status=STATUS_MANUAL,
        homepage="https://www.meteo.gov.gh/Services/agrometeorology/",
        integration_notes="Advisories live in an admin-editable table with published/valid dates and source URLs, surfaced on the weather and planting tabs.",
        limitations="Only advisories an administrator has entered appear. Absence of an advisory is not evidence that conditions are normal.",
        verification="Public pages reachable; no supported machine-readable API found, so a manual source-linked workflow is used instead.",
    ),
    ProviderSpec(
        id="wapor",
        name="FAO WaPOR v3",
        category="water",
        variables="Actual evapotranspiration and interception (AETI), reference ET, net primary production, soil moisture",
        resolution="100 m / 300 m",
        temporal="Dekadal, monthly, seasonal",
        coverage="Africa and Near East, includes Ghana",
        licence="CC BY-NC-SA 4.0 (WaPOR v3)",
        licence_notes="Non-commercial and share-alike. Commercial use requires permission from FAO.",
        access="Public no-key API (currently returning HTTP 502); Digital Earth Africa mirror inspected but contains only STAC metadata, not rasters",
        key_required=False,
        status=STATUS_UNAVAILABLE,
        homepage="https://www.fao.org/in-action/remote-sensing-for-water-productivity/wapor-data-access/en",
        integration_notes="The adapter probes the WaPOR catalogue and, while the service is down, reports 'unavailable' rather than substituting a modelled value. Water advice therefore relies only on rainfall, ET₀ and crop context.",
        limitations="Unavailable at verification time; no WaPOR values are shown. Irrigation need is a planning estimate, not a measured water requirement.",
        verification="https://io.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR returned HTTP 502 on repeated attempts.",
        commercial_note="Non-commercial licence; contact FAO for commercial use.",
    ),
    ProviderSpec(
        id="gaez",
        name="FAO/IIASA GAEZ v5 crop suitability",
        category="suitability",
        variables="Regional crop suitability and potential yield classes",
        resolution="~10 km (5 arc-minute) regional products",
        temporal="Long-term climate normals",
        coverage="Global; Ghana covered by Africa regional files",
        licence="CC BY-NC-SA 3.0 IGO (GAEZ v5)",
        licence_notes="Non-commercial, share-alike, attribution to FAO/IIASA.",
        access="Bulk download only; no machine API for a farm-sized subset was identified",
        key_required=False,
        status=STATUS_UNAVAILABLE,
        homepage="https://www.fao.org/land-water/resources/tools/databases/gaez/en",
        integration_notes="No GAEZ raster is queried in this release. Crop ranking comes from a documented local rule engine using SoilGrids, CHIRPS climatology, terrain and the cited crop reference ranges, and is labelled as rule-based screening.",
        limitations="Not a GAEZ query. Suitability categories are indicative screening outputs, never a potential-yield promise.",
        verification="gaez.fao.org is reachable but exposes an interactive download workflow without a documented machine endpoint for polygon subsets.",
        commercial_note="Non-commercial licence applies to the underlying GAEZ product.",
    ),
    ProviderSpec(
        id="admin_market",
        name="District market prices and extension alerts (administrator-entered)",
        category="market",
        variables="Observed market prices (GHS per unit), sourced extension alerts",
        resolution="District / market",
        temporal="As entered, with observation date",
        coverage="Ghana",
        licence="Entered by the deployment operator; source note required per record",
        licence_notes="No free, reliable, licensed price API was verified, so no live price integration exists. Records require a source note and observation date.",
        access="Admin REST endpoints behind GFI_ADMIN_TOKEN",
        key_required=True,
        status=STATUS_MANUAL,
        homepage="https://mofa.gov.gh/",
        integration_notes="Prices are displayed with their observation date and source note, and never used to fabricate economic suitability scores unless the record is fresh and complete.",
        limitations="Only what an administrator has entered is shown; no price is inferred or interpolated.",
        verification="Deliberately not connected to any third-party price feed: none was verified as free, reliable and licensed for this use.",
    ),
)


def provider_map() -> dict[str, ProviderSpec]:
    return {spec.id: spec for spec in PROVIDERS}


def registry_payload() -> dict[str, Any]:
    return {
        "verified_on": VERIFIED_ON,
        "providers": [spec.as_dict() for spec in PROVIDERS],
    }
