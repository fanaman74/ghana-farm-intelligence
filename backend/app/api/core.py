"""Core read-only endpoints: health, metadata, catalogue, places, basemap, chips."""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from ..config import APP_NAME, GHANA_BOUNDS, GHANA_CENTER, SEED_DIR, VERSION, settings
from ..db import provider_health_all
from ..geo import admin
from ..geo import places as places_service
from ..providers.registry import registry_payload
from ..utils import ghana_time_label
from ..engine import crops as crop_kb

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    from ..db import connect

    database_ok = True
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:  # noqa: BLE001
        database_ok = False
    return {
        "status": "ok" if database_ok else "degraded",
        "app": APP_NAME,
        "version": VERSION,
        "database": "ok" if database_ok else "unavailable",
        "time": ghana_time_label(),
        "network_access": settings.allow_network,
        "data_dir": str(settings.data_dir),
        "seed_available": (SEED_DIR / "ghana_places.json.gz").exists(),
    }


@router.get("/meta")
def meta() -> dict[str, Any]:
    return {
        "app": APP_NAME,
        "version": VERSION,
        "environment": settings.env,
        "time": ghana_time_label(),
        "ghana_bounds": GHANA_BOUNDS,
        "ghana_center": GHANA_CENTER,
        "units": {"area": "hectares", "distance": "metres", "rainfall": "millimetres", "temperature": "°C", "currency": "GHS"},
        "timezone": "Africa/Accra",
        "min_area_ha": settings.min_area_ha,
        "max_area_ha": settings.max_area_ha,
        "demo_data_enabled": settings.demo_enabled,
        "languages": [
            {"code": "en", "label": "English", "status": "reviewed"},
            {"code": "tw", "label": "Twi (Akan)", "status": "interface_only_unreviewed"},
            {"code": "ee", "label": "Ewe", "status": "interface_only_unreviewed"},
            {"code": "dag", "label": "Dagbani", "status": "interface_only_unreviewed"},
        ],
        "disclaimer": (
            "Farm Intelligence shows sourced estimates and their limitations. It cannot replace a soil test, "
            "field inspection or extension advice."
        ),
    }


@router.get("/catalog/providers")
def providers() -> dict[str, Any]:
    payload = registry_payload()
    health = provider_health_all()
    for provider in payload["providers"]:
        probe_state = health.get(provider["id"])
        provider["last_checked"] = probe_state
    return payload


@router.get("/catalog/providers/status")
def provider_status() -> dict[str, Any]:
    from ..providers import base

    probes = [
        ("openmeteo_forecast", "https://api.open-meteo.com/v1/forecast",
         {"latitude": 7.9, "longitude": -1.0, "daily": "precipitation_sum", "forecast_days": 1}),
        ("sentinel2", "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a", None),
        ("wapor", "https://io.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR", None),
    ]
    results = [base.probe(provider, url, params=params) for provider, url, params in probes]
    return {"checked_at": ghana_time_label(), "results": results, "cached_health": provider_health_all()}


@router.get("/catalog/crops")
def crops() -> dict[str, Any]:
    kb = crop_kb.load_kb()
    return {
        "crops": crop_kb.crop_summaries(),
        "zones": admin.AGRO_ECOLOGICAL_ZONES,
        "generated_at": kb.get("generated_at"),
        "currency_note": kb.get("currency_note"),
        "sources": kb.get("sources"),
        "note": (
            "Crop reference ranges are compiled from cited public sources. They are planning information, not "
            "extension advice, and contain no yields, prices, fertiliser rates or pesticide dosages."
        ),
    }


@router.get("/catalog/crops/{crop_id}")
def crop_detail(crop_id: str) -> dict[str, Any]:
    crop = crop_kb.get_crop(crop_id)
    if crop is None:
        raise HTTPException(status_code=404, detail="Unknown crop")
    return {"crop": crop, "sources": crop_kb.sources_for(crop.get("source_ids", []))}


@router.get("/catalog/zones")
def zones() -> dict[str, Any]:
    return {
        "zones": admin.AGRO_ECOLOGICAL_ZONES,
        "method": (
            "Agro-ecological zone is approximated from the polygon's 1991–2020 CHIRPS annual rainfall, "
            "latitude and coast distance, using MoFA's zone descriptions. Boundaries are gradual in reality."
        ),
    }


@router.get("/sources/attribution")
def attribution() -> dict[str, Any]:
    payload = registry_payload()
    return {
        "verified_on": payload["verified_on"],
        "providers": payload["providers"],
        "code_licence": "Application code: MIT. See LICENSE.",
        "notes": [
            "Each dataset keeps its own licence and attribution requirements.",
            "OpenStreetMap data is © OpenStreetMap contributors, ODbL 1.0.",
            "Copernicus Sentinel data are free and open under the Copernicus data policy.",
            "SoilGrids is CC BY 4.0; attribution to ISRIC is required.",
            "Open-Meteo data are CC BY 4.0; the hosted free tier is non-commercial.",
            "CHIRPS is public domain (UCSB Climate Hazards Center).",
            "WaPOR is CC BY-NC-SA 4.0 and non-commercial.",
            "GAEZ is CC BY-NC-SA 3.0 IGO and is not queried in this release.",
        ],
    }


@router.get("/places/search")
def search_places(q: str = Query(min_length=1, max_length=80), limit: int = Query(default=12, ge=1, le=30)) -> dict[str, Any]:
    return places_service.search(q, limit=limit)


@router.get("/places/reverse")
def reverse_place(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)) -> dict[str, Any]:
    return places_service.reverse(lon, lat)


@router.get("/geo/regions")
def regions() -> dict[str, Any]:
    return {"regions": admin.region_names()}


@router.get("/geo/districts")
def districts(region: str | None = None) -> dict[str, Any]:
    return {"districts": admin.district_names(region)}


@router.get("/geo/boundaries/{level}")
def boundaries(level: str) -> JSONResponse:
    try:
        payload = admin.boundaries(level.upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=86400"})


def _serve_seed_gzip(filename: str) -> FileResponse:
    path = SEED_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Seed data {filename} has not been built on this deployment.")
    return FileResponse(
        path,
        media_type="application/geo+json",
        headers={
            "Content-Encoding": "gzip",
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("/basemap/roads")
def basemap_roads() -> FileResponse:
    return _serve_seed_gzip("ghana_roads.geojson.gz")


@router.get("/basemap/water")
def basemap_water() -> FileResponse:
    return _serve_seed_gzip("ghana_water.geojson.gz")


@router.get("/basemap/places")
def basemap_places(limit: int = Query(default=4000, ge=1, le=20000)) -> JSONResponse:
    path = SEED_DIR / "ghana_places.json.gz"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Place seed has not been built on this deployment.")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = __import__("json").load(handle)
    places = payload.get("places", [])
    # Keep towns and larger settlements for label rendering at national scale.
    priority = {"city": 0, "town": 1, "region_centre": 1, "district_centre": 1, "village": 2, "hamlet": 3}
    places = sorted(places, key=lambda item: (priority.get(item.get("type"), 4), -(item.get("population") or 0)))
    trimmed = [
        {
            "name": item.get("name"),
            "type": item.get("type"),
            "lat": item.get("lat"),
            "lon": item.get("lon"),
            "region": item.get("region"),
        }
        for item in places[:limit]
    ]
    return JSONResponse(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [item["lon"], item["lat"]]},
                    "properties": item,
                }
                for item in trimmed
            ],
            "properties": {"source": payload.get("source"), "generated_at": payload.get("generated_at")},
        },
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/chips/{filename}")
def chip(filename: str) -> FileResponse:
    safe = Path(filename).name
    path = settings.cache_dir / "chips" / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chip not found")
    media = "image/jpeg" if safe.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/tiles/terrain")
def terrain_tile_source(request: Request) -> dict[str, Any]:
    """Describe the optional 3D terrain source (client fetches tiles directly)."""
    return {
        "provider": "Mapzen/AWS terrain tiles (Terrarium encoding)",
        "url": "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
        "encoding": "terrarium",
        "max_zoom": 12,
        "licence": "Mapzen terrain tiles — CC BY 4.0 / public domain elevation sources; attribution required",
        "note": "Loaded by the browser only when 3D terrain is enabled.",
    }
