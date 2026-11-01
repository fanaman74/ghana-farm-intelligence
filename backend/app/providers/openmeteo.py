"""Open-Meteo forecast adapter (no key; non-commercial hosted tier).

The API returns the model grid cell nearest the farm centroid. That cell is
stored and shown explicitly — a farm forecast is a grid-cell estimate, not an
on-farm observation.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from ..config import GH_TZ, settings
from ..db import cache_get, cache_put
from ..geo.geodesy import centroid_lonlat, ensure_shapely
from ..utils import iso_now, stable_hash
from .base import ProviderError, Section, Source, STATUS_OK, STATUS_STALE, request
from .registry import provider_map

log = logging.getLogger(__name__)

PROVIDER = "openmeteo_forecast"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

CURRENT_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "soil_moisture_0_to_10cm",
    "soil_temperature_0_to_10cm",
    "is_day",
]
HOURLY_FIELDS = [
    "precipitation",
    "precipitation_probability",
    "temperature_2m",
    "relative_humidity_2m",
    "et0_fao_evapotranspiration",
    "soil_moisture_0_to_10cm",
    "wind_speed_10m",
]
DAILY_FIELDS = [
    "precipitation_sum",
    "precipitation_probability_max",
    "temperature_2m_max",
    "temperature_2m_min",
    "et0_fao_evapotranspiration",
    "relative_humidity_2m_mean",
    "wind_speed_10m_max",
    "weather_code",
]

WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    80: "Light showers",
    81: "Moderate showers",
    82: "Violent showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Strong thunderstorm with hail",
}


def _cache_key(lat: float, lon: float) -> str:
    return f"{PROVIDER}:{stable_hash([round(lat, 2), round(lon, 2), 'v1'])}"


def _fetch(lat: float, lon: float, forecast_days: int = 16, past_days: int = 5) -> dict[str, Any]:
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "timezone": "Africa/Accra",
        "current": ",".join(CURRENT_FIELDS),
        "hourly": ",".join(HOURLY_FIELDS),
        "daily": ",".join(DAILY_FIELDS),
        "forecast_days": forecast_days,
        "past_days": past_days,
        "models": "best_match",
        "wind_speed_unit": "kmh",
    }
    payload = request(PROVIDER, "GET", FORECAST_URL, params=params)
    if not isinstance(payload, dict) or "daily" not in payload:
        raise ProviderError(PROVIDER, "invalid_payload", "Open-Meteo returned an unexpected payload")
    # Cache per grid cell; 30 min TTL keeps forecasts fresh.
    cache_put(_cache_key(lat, lon), PROVIDER, payload, settings.weather_cache_ttl_s)
    return payload


def _with_cache(lat: float, lon: float) -> tuple[dict[str, Any], bool]:
    entry = cache_get(_cache_key(lat, lon))
    if entry and not entry["expired"]:
        return entry["payload"], False
    try:
        return _fetch(lat, lon), False
    except Exception:
        if entry is not None:
            log.warning("Open-Meteo refresh failed; using cached forecast from %s", entry["created_at"])
            return entry["payload"], True
        raise


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _daily_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    rows = []
    for index, iso_day in enumerate(times):
        def pick(field: str):
            series = daily.get(field) or []
            return series[index] if index < len(series) else None

        rows.append(
            {
                "date": iso_day,
                "precipitation_mm": _num(pick("precipitation_sum")),
                "precipitation_probability": _num(pick("precipitation_probability_max")),
                "temperature_max_c": _num(pick("temperature_2m_max")),
                "temperature_min_c": _num(pick("temperature_2m_min")),
                "et0_mm": _num(pick("et0_fao_evapotranspiration")),
                "humidity_mean_pct": _num(pick("relative_humidity_2m_mean")),
                "wind_max_kmh": _num(pick("wind_speed_10m_max")),
                "weather_code": pick("weather_code"),
                "weather": WMO_CODES.get(pick("weather_code"), "—"),
            }
        )
    return rows


def _hourly_rows(payload: dict[str, Any], limit_hours: int = 48) -> list[dict[str, Any]]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    rows = []
    for index, stamp in enumerate(times[:limit_hours]):
        def pick(field: str):
            series = hourly.get(field) or []
            return series[index] if index < len(series) else None

        rows.append(
            {
                "time": stamp,
                "precipitation_mm": _num(pick("precipitation")),
                "precipitation_probability": _num(pick("precipitation_probability")),
                "temperature_c": _num(pick("temperature_2m")),
                "humidity_pct": _num(pick("relative_humidity_2m")),
                "et0_mm": _num(pick("et0_fao_evapotranspiration")),
                "soil_moisture_0_10cm": _num(pick("soil_moisture_0_to_10cm")),
                "wind_kmh": _num(pick("wind_speed_10m")),
            }
        )
    return rows


def _analytics(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    future = [row for row in rows if row["date"] >= today.isoformat()]
    next7 = future[:7]
    next14 = future[:14]
    precip7 = sum((row["precipitation_mm"] or 0) for row in next7)
    et0_7 = sum((row["et0_mm"] or 0) for row in next7)
    rain_days_7 = sum(1 for row in next7 if (row["precipitation_mm"] or 0) >= 1.0)

    # Dry spell: consecutive days from today with < 1 mm and low rain probability.
    dry_spell = 0
    for row in future:
        if (row["precipitation_mm"] or 0) < 1.0 and (row["precipitation_probability"] or 0) < 50:
            dry_spell += 1
        else:
            break

    heat_days = [row["date"] for row in next14 if (row["temperature_max_c"] or 0) >= 35]
    heavy_days = [
        {"date": row["date"], "mm": row["precipitation_mm"]}
        for row in next14
        if (row["precipitation_mm"] or 0) >= 30
    ]
    rainy_window = None
    if len(next14) >= 3:
        best = None
        for start in range(0, max(0, len(next14) - 2)):
            window = next14[start:start + 3]
            total = sum((row["precipitation_mm"] or 0) for row in window)
            days = sum(1 for row in window if (row["precipitation_mm"] or 0) >= 1)
            if days >= 2 and (best is None or total > best["total"]):
                best = {"start": window[0]["date"], "total": round(total, 1), "rain_days": days}
        rainy_window = best

    return {
        "next_7_days": {
            "precipitation_mm": round(precip7, 1),
            "et0_mm": round(et0_7, 1),
            "rain_days": rain_days_7,
            "water_balance_mm": round(precip7 - et0_7, 1),
            "temperature_max_c": max((row["temperature_max_c"] or -99) for row in next7) if next7 else None,
            "temperature_min_c": min((row["temperature_min_c"] or 99) for row in next7) if next7 else None,
        },
        "dry_spell_days": dry_spell,
        "heat_days": heat_days,
        "heavy_rain_days": heavy_days,
        "best_3_day_rain_window": rainy_window,
    }


def analyse(geom, *, forecast_days: int = 16) -> Section:
    geom = ensure_shapely(geom)
    lat, lon = centroid_lonlat(geom)
    payload, from_stale = _with_cache(lat, lon)

    rows = _daily_rows(payload)
    today = datetime.now(GH_TZ).date()
    analytics = _analytics(rows, today)
    grid = {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "elevation_m": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "model": (payload.get("daily_units") or {}).get("__model__") or "best_match (Open-Meteo)",
        "nominal_resolution_km": 11,
    }
    current = payload.get("current") or {}
    current_view = {
        "time": current.get("time"),
        "temperature_c": _num(current.get("temperature_2m")),
        "apparent_temperature_c": _num(current.get("apparent_temperature")),
        "humidity_pct": _num(current.get("relative_humidity_2m")),
        "precipitation_mm": _num(current.get("precipitation")),
        "weather_code": current.get("weather_code"),
        "weather": WMO_CODES.get(current.get("weather_code"), "—"),
        "cloud_cover_pct": _num(current.get("cloud_cover")),
        "wind_kmh": _num(current.get("wind_speed_10m")),
        "wind_direction_deg": _num(current.get("wind_direction_10m")),
        "soil_moisture_0_10cm": _num(current.get("soil_moisture_0_to_10cm")),
        "soil_temperature_0_10cm": _num(current.get("soil_temperature_0_to_10cm")),
        "is_day": current.get("is_day"),
    }

    warnings: list[str] = []
    status = STATUS_STALE if from_stale else STATUS_OK
    message = None
    if from_stale:
        message = "Open-Meteo could not be refreshed; the last successful forecast is shown with its retrieval time."

    average_distance_km = _distance_km(lat, lon, grid["latitude"], grid["longitude"])
    if average_distance_km and average_distance_km > 10:
        warnings.append(
            f"The model grid cell is about {average_distance_km:.0f} km from the farm centroid; "
            "treat rainfall as an area estimate."
        )

    return Section(
        key="weather",
        status=status,
        data={
            "current": current_view,
            "daily": rows,
            "hourly": _hourly_rows(payload, 48),
            "analytics": analytics,
            "grid": grid,
            "grid_distance_km": average_distance_km,
            "forecast_days": len(rows),
            "source_kind": "model_forecast",
            "notes": [
                "Forecast values describe the Open-Meteo model grid cell, not a measurement on the farm.",
                "Forecast skill for localised convective rainfall in Ghana is limited beyond ~5 days.",
            ],
        },
        sources=[
            Source(
                provider_id=PROVIDER,
                dataset="Open-Meteo forecast (best_match global model)",
                url="https://open-meteo.com/en/docs",
                licence="CC BY 4.0 (data); hosted free tier is non-commercial",
                resolution=f"~{grid['nominal_resolution_km']} km model grid",
                period=f"{rows[0]['date'] if rows else '—'} to {rows[-1]['date'] if rows else '—'}",
                method="Daily 16-day and hourly 48-hour request at the farm centroid, timezone Africa/Accra",
                limitations="Model output for a grid cell. Not an on-farm observation; tropical convective rainfall is often localised.",
                access_method="HTTPS JSON API (no key)",
                key_required=False,
            )
        ],
        message=message,
        warnings=warnings,
        retrieved_at=iso_now(),
    )


def _distance_km(lat1: float, lon1: float, lat2: Any, lon2: Any) -> float | None:
    from pyproj import Geod

    if lat2 is None or lon2 is None:
        return None
    geod = Geod(ellps="WGS84")
    _, _, distance = geod.inv(lon1, lat1, float(lon2), float(lat2))
    return round(abs(distance) / 1000.0, 1)


def climate_normals(geom) -> dict[str, Any]:
    """Five-year ERA5 temperature normals used by the crop rule engine.

    Rainfall normals come from the bundled CHIRPS climatology instead, so this
    function only needs temperature.
    """
    geom = ensure_shapely(geom)
    lat, lon = centroid_lonlat(geom)
    cache_key = f"{PROVIDER}:climate:{stable_hash([round(lat, 2), round(lon, 2)])}"
    entry = cache_get(cache_key)
    if entry and not entry["expired"]:
        return entry["payload"]
    params = {
        "latitude": round(lat, 3),
        "longitude": round(lon, 3),
        "start_date": "2015-01-01",
        "end_date": "2019-12-31",
        "daily": "temperature_2m_mean",
        "timezone": "Africa/Accra",
    }
    payload = request(PROVIDER, "GET", ARCHIVE_URL, params=params)
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    values = daily.get("temperature_2m_mean") or []
    monthly: dict[int, list[float]] = {month: [] for month in range(1, 13)}
    for stamp, value in zip(times, values):
        if value is None:
            continue
        month = int(stamp[5:7])
        monthly[month].append(float(value))
    monthly_mean = {
        str(month): round(sum(vals) / len(vals), 2) if vals else None for month, vals in monthly.items()
    }
    all_values = [v for vals in monthly.values() for v in vals]
    result = {
        "monthly_mean_temperature_c": monthly_mean,
        "annual_mean_temperature_c": round(sum(all_values) / len(all_values), 2) if all_values else None,
        "period": "2015-01-01 to 2019-12-31",
        "grid": {"latitude": payload.get("latitude"), "longitude": payload.get("longitude")},
        "source": {
            "provider_id": PROVIDER,
            "dataset": "ERA5 / ERA5-Land reanalysis via Open-Meteo archive API",
            "url": "https://open-meteo.com/en/docs/historical-weather-api",
            "licence": "CC BY 4.0",
            "limitations": "Reanalysis normals for a ~9–25 km grid cell, not a farm record.",
        },
    }
    cache_put(cache_key, PROVIDER, result, 30 * 24 * 3600)
    return result
