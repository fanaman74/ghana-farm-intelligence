"""Analysis orchestration: real polygon queries, partial progress, caching."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import GH_TZ, settings
from .db import (
    analysis_cache_get,
    analysis_cache_put,
    get_conn,
    job_create,
    job_get,
    job_update,
    provider_health_all,
)
from .engine import crops as crop_kb
from .engine import recommendations as engine_recs
from .engine import suitability
from .geo import admin
from .geo.geodesy import GeometryError, ensure_shapely, validate_geometry
from .providers import chirps, landcover, openmeteo, sentinel2, soilgrids, terrain, wapor
from .providers.base import Section, Source
from .utils import iso_now, parse_date, stable_hash

log = logging.getLogger(__name__)

ANALYSIS_VERSION = 2
_EXECUTOR = ThreadPoolExecutor(max_workers=max(2, settings.job_workers), thread_name_prefix="gfi-analysis")

SECTION_LABELS = {
    "weather": "Forecast and current conditions",
    "soil": "Soil profile",
    "rainfall": "Rainfall history",
    "terrain": "Terrain",
    "landcover": "Land-cover baseline",
    "vegetation": "Sentinel-2 vegetation",
    "water": "Water balance",
    "crops": "Crop suitability",
    "calendar": "Crop calendar",
}


# --------------------------------------------------------------------------- #
# Farm loading
# --------------------------------------------------------------------------- #
def load_farm(farm_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM farms WHERE id=?", (farm_id,)).fetchone()
        if row is None:
            raise KeyError(farm_id)
        fields = conn.execute("SELECT * FROM farm_fields WHERE farm_id=? ORDER BY created_at", (farm_id,)).fetchall()
        tests = conn.execute("SELECT * FROM soil_tests WHERE farm_id=? ORDER BY sampled_on DESC", (farm_id,)).fetchall()
        records = conn.execute(
            "SELECT * FROM farm_records WHERE farm_id=? ORDER BY recorded_at DESC LIMIT 50", (farm_id,)
        ).fetchall()
        advisories = conn.execute(
            "SELECT * FROM advisories WHERE valid_to IS NULL OR valid_to>=? ORDER BY published_at DESC LIMIT 5",
            (datetime.now(GH_TZ).date().isoformat(),),
        ).fetchall()
    farm = dict(row)
    farm["geometry"] = json.loads(row["geometry"])
    farm["fields"] = [
        {**dict(field), "geometry": json.loads(field["geometry"])} for field in fields
    ]
    farm["soil_tests"] = [dict(test) for test in tests]
    farm["records"] = [
        {**dict(record), "payload": json.loads(record["payload"]) if record["payload"] else None}
        for record in records
    ]
    farm["advisories"] = [dict(advisory) for advisory in advisories]
    return farm


def farm_geometry(farm: dict[str, Any], field_id: str | None = None):
    if field_id:
        for field in farm["fields"]:
            if field["id"] == field_id:
                return ensure_shapely(field["geometry"]), field["area_ha"], f"field:{field['name']}"
        raise KeyError(field_id)
    if farm["fields"]:
        geoms = [field["geometry"] for field in farm["fields"]]
        return ensure_shapely({"type": "GeometryCollection", "geometries": geoms} if len(geoms) > 1 else geoms[0]), sum(
            field["area_ha"] for field in farm["fields"]
        ), f"union of {len(farm['fields'])} fields"
    return ensure_shapely(farm["geometry"]), farm["area_ha"], "farm boundary"


# --------------------------------------------------------------------------- #
# Section helpers
# --------------------------------------------------------------------------- #
def _merge_measured_soil(soil_section: Section, farm: dict[str, Any]) -> None:
    tests = farm.get("soil_tests") or []
    if not tests:
        return
    latest = tests[0]
    measured = {
        "sampled_on": latest.get("sampled_on"),
        "lab_name": latest.get("lab_name"),
        "depth_cm": [latest.get("depth_from_cm"), latest.get("depth_to_cm")],
        "ph": latest.get("ph"),
        "soc_gkg": latest.get("soc_gkg"),
        "total_n_gkg": latest.get("total_n_gkg"),
        "p_mgkg": latest.get("p_mgkg"),
        "k_mgkg": latest.get("k_mgkg"),
        "cec_cmolkg": latest.get("cec_cmolkg"),
        "clay_pct": latest.get("clay_pct"),
        "sand_pct": latest.get("sand_pct"),
        "silt_pct": latest.get("silt_pct"),
        "texture_class": latest.get("texture_class"),
        "source_url": latest.get("source_url"),
        "notes": latest.get("notes"),
        "source_kind": "laboratory_measurement",
    }
    soil_section.data["measured"] = measured
    soil_section.data["precedence"] = "Measured laboratory values take precedence over SoilGrids estimates in guidance."
    soil_section.warnings.append(
        "Laboratory soil results are recorded for this farm and are used in guidance in preference to model estimates."
    )


def _empty_section(key: str, message: str) -> Section:
    return Section(key=key, status="unavailable", data={"reason": "not_run"}, message=message)


def _source_registry() -> list[dict[str, Any]]:
    from .providers.registry import PROVIDERS

    return [spec.as_dict() for spec in PROVIDERS]


def _data_status(sections: dict[str, Section]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    unavailable: list[str] = []
    stale: list[str] = []
    for key, section in sections.items():
        counts[section.status] = counts.get(section.status, 0) + 1
        if section.status in ("unavailable", "error"):
            unavailable.append(key)
        if section.status == "stale":
            stale.append(key)
    core = [key for key in ("soil", "weather", "rainfall") if key in sections]
    core_ok = [key for key in core if sections[key].status in ("ok", "stale")]
    return {
        "sections": counts,
        "unavailable": unavailable,
        "stale": stale,
        "core_complete": len(core_ok) == len(core) and bool(core),
        "provider_health": provider_health_all(),
    }


def _provenance(sections: dict[str, Section]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for section in sections.values():
        for source in section.sources:
            key = (source.provider_id, source.dataset)
            if key in seen:
                continue
            seen.add(key)
            payload = source.as_dict()
            payload["fetched_at"] = section.retrieved_at
            payload["section"] = section.key
            payload["status"] = section.status
            output.append(payload)
    return output


# --------------------------------------------------------------------------- #
# Main build
# --------------------------------------------------------------------------- #
def check_analysis_area(area_ha: float, limit_ha: float) -> None:
    """Refuse an analysis that would read an unreasonable number of pixels.

    A 50,000 ha boundary (the storage limit) is ~5 M pixels per Sentinel-2 band,
    read as float64, several bands at a time, on a 3-job pool. The job would
    exhaust memory or time out with no explanation; refusing says why.
    """
    if limit_ha and area_ha > limit_ha:
        raise GeometryError(
            "area_too_large_for_analysis",
            f"This boundary is {area_ha:,.0f} ha, above the {limit_ha:,.0f} ha analysis limit "
            "(GFI_MAX_ANALYSIS_HA). Split it into fields and analyse each one.",
            {"area_ha": area_ha, "max_analysis_ha": limit_ha},
        )


def check_deadline(elapsed_s: float, limit_s: float) -> None:
    """Fail a run that has exceeded its wall-clock budget.

    A thread blocked in a provider read cannot be cancelled cleanly, but between
    sections the job can stop and say so instead of holding a worker — and the
    client's progress bar — indefinitely.
    """
    if limit_s and elapsed_s > limit_s:
        raise GeometryError(
            "analysis_timeout",
            f"This analysis exceeded its {limit_s:.0f}-second budget. Try a smaller boundary, or raise "
            "GFI_ANALYSIS_TIMEOUT on the server.",
            {"elapsed_s": round(elapsed_s, 1), "limit_s": limit_s},
        )


def build_analysis(
    farm: dict[str, Any],
    *,
    field_id: str | None = None,
    crop_id: str | None = None,
    planting_date: str | None = None,
    management_level: str = "medium",
    progress: Callable[[float, str, dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    geom, area_ha, geometry_label = farm_geometry(farm, field_id)
    info = validate_geometry(geom)
    check_analysis_area(info.area_ha, settings.max_analysis_ha)

    crop_id = crop_id or farm.get("crop_id")
    planting = parse_date(planting_date) or parse_date(farm.get("planting_date"))
    irrigation = farm.get("irrigation") or "rainfed"
    today = datetime.now(GH_TZ).date()

    location = admin.locate(info.centroid_lon, info.centroid_lat)
    annual = chirps.annual_rainfall(geom) or {}
    zone = admin.classify_zone(
        annual_rainfall_mm=annual.get("annual_mm"),
        region=location.get("region"),
        lat=info.centroid_lat,
        lon=info.centroid_lon,
    )

    sections: dict[str, Section] = {}
    steps_done = 0
    total_steps = 8

    def emit(message: str, partial: bool = True) -> None:
        nonlocal steps_done
        if progress:
            progress(min(0.95, steps_done / total_steps), message, _partial_payload() if partial else None)

    def _partial_payload() -> dict[str, Any]:
        return {
            "sections": {key: section.as_dict() for key, section in sections.items()},
            "partial": True,
        }

    def budget() -> None:
        check_deadline(time.perf_counter() - started, settings.analysis_timeout_s)

    emit("Reading forecast and terrain", False)

    # Fast, independent sections in parallel.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(openmeteo.analyse, geom): "weather",
            pool.submit(soilgrids.analyse, geom): "soil",
            pool.submit(terrain.analyse, geom, annual_rainfall_mm=annual.get("annual_mm")): "terrain",
            pool.submit(landcover.analyse, geom): "landcover",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                section = future.result()
            except Exception as exc:  # noqa: BLE001
                section = _failed_section(key, exc)
            if key == "soil":
                _merge_measured_soil(section, farm)
            sections[key] = section
            steps_done += 1
            emit(f"{SECTION_LABELS.get(key, key)} ready")

    emit("Reading CHIRPS rainfall history")
    try:
        sections["rainfall"] = chirps.analyse(geom)
    except Exception as exc:  # noqa: BLE001
        sections["rainfall"] = _failed_section("rainfall", exc)
    steps_done += 1
    emit("Rainfall ready")
    budget()

    emit("Fetching Sentinel-2 scenes")
    try:
        sections["vegetation"] = sentinel2.analyse(geom)
    except Exception as exc:  # noqa: BLE001
        sections["vegetation"] = _failed_section("vegetation", exc)
    steps_done += 1
    emit("Satellite imagery ready")
    budget()

    emit("Checking WaPOR")
    try:
        sections["water"] = wapor.analyse(geom)
    except Exception as exc:  # noqa: BLE001
        sections["water"] = _failed_section("water", exc)
    steps_done += 1
    budget()

    # ---- Crop suitability -------------------------------------------------
    weather_data = sections["weather"].data
    soil_data = sections["soil"].data
    modelled = soil_data.get("modelled") or {}
    profile = modelled.get("profile_0_30") or {}
    measured = soil_data.get("measured") or {}
    soil_context = {
        "ph": measured.get("ph") or (profile.get("phh2o") or {}).get("value"),
        "texture_class": measured.get("texture_class") or modelled.get("texture_class"),
        "sand": measured.get("sand_pct") or modelled.get("texture_fractions", {}).get("sand"),
        "clay": measured.get("clay_pct") or modelled.get("texture_fractions", {}).get("clay"),
        "silt": measured.get("silt_pct") or modelled.get("texture_fractions", {}).get("silt"),
        "soc": measured.get("soc_gkg") or (profile.get("soc") or {}).get("value"),
        "cec": profile.get("cec", {}).get("value"),
        "valid_fraction": (modelled.get("coverage") or {}).get("valid_fraction"),
        "sub_field_detail": (modelled.get("coverage") or {}).get("sub_field_detail"),
    }
    try:
        temperature = openmeteo.climate_normals(geom)
    except Exception as exc:  # noqa: BLE001
        log.info("Temperature normals unavailable: %s", exc)
        temperature = {}

    context = {
        "today": today,
        "zone": zone,
        "soil": soil_context,
        "measured_ph": measured.get("ph"),
        "monthly_rainfall_mm": (sections["rainfall"].data.get("climatology") or {}).get("monthly_mean_mm"),
        "monthly_mean_temperature_c": temperature.get("monthly_mean_temperature_c"),
        "mean_temperature_c": temperature.get("annual_mean_temperature_c"),
        "terrain": sections["terrain"].data,
        "irrigation": irrigation,
        "management_level": management_level,
        "planting_date": planting,
        "annual_rainfall_mm": annual.get("annual_mm"),
    }
    ranking = suitability.rank_crops(context, limit=3)
    selected_crop_kb = crop_kb.get_crop(crop_id)
    selected_eval = None
    if selected_crop_kb:
        selected_eval = suitability.evaluate_crop(selected_crop_kb, context)
        if selected_eval["category"] != "insufficient_data" and selected_eval["id"] not in {
            item["id"] for item in ranking["ranking"]
        }:
            ranking["ranking"] = [selected_eval] + ranking["ranking"][:2]
    sections["crops"] = Section(
        key="crops",
        status="ok" if ranking["ranking"] else "unavailable",
        data={
            "selected": selected_eval,
            "selected_id": crop_id,
            "ranking": ranking["ranking"],
            "all_evaluations": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "score": item["score"],
                    "category": item["category"],
                    "category_label": item["category_label"],
                    "confidence": item["confidence"],
                    "perennial": item["perennial"],
                }
                for item in ranking["all"]
            ],
            "insufficient": ranking["insufficient"],
            "missing_inputs": ranking["missing_inputs"],
            "method": ranking["method"],
            "inputs": {
                "soil_ph": soil_context["ph"],
                "soil_texture": soil_context["texture_class"],
                "annual_rainfall_mm": annual.get("annual_mm"),
                "monthly_rainfall_mm": context["monthly_rainfall_mm"],
                "mean_temperature_c": context["mean_temperature_c"],
                "slope_percent": (sections["terrain"].data.get("slope_percent") or {}).get("mean"),
                "irrigation": irrigation,
                "zone": zone.get("name"),
            },
            "source_kind": "rule_engine",
            "notes": [
                "Rule-based screening from cited crop ranges, not a GAEZ raster query and not a yield forecast.",
                "Market and economic suitability are not scored because no verified, licensed price feed is connected.",
            ],
        },
        sources=[
            Source(
                provider_id="ghana_crop_reference",
                dataset="Ghana crop reference database (FAO EcoCrop, FAO crop calendar, FAO-56, MoFA, GIEWS)",
                url="https://ecocrop.apps.fao.org/ecocrop/srv/en/home",
                licence="Compiled reference data; each source's own licence applies (see attribution page)",
                resolution="Crop-level reference ranges",
                method="Published crop ranges combined with farm soil, rainfall, temperature and terrain",
                limitations="Indicative ranges; local varieties and field measurements take precedence.",
            )
        ],
        warnings=[],
        retrieved_at=iso_now(),
    )
    steps_done += 1

    # ---- Calendar ---------------------------------------------------------
    crop_for_calendar = selected_crop_kb or (
        crop_kb.get_crop(ranking["ranking"][0]["id"]) if ranking["ranking"] else None
    )
    forecast_dry = (weather_data.get("analytics") or {}).get("dry_spell_days")
    calendar = engine_recs.calendar_tasks(crop_for_calendar, planting, today, zone, forecast_dry)
    sections["calendar"] = Section(
        key="calendar",
        status="ok",
        data={
            **calendar,
            "crop_id": (crop_for_calendar or {}).get("id"),
            "crop_name": (crop_for_calendar or {}).get("name"),
            "planting_date": planting.isoformat() if planting else None,
            "season": (selected_eval or {}).get("planting_window"),
            "zone": zone.get("name"),
            "notes": [
                "Dates are derived from the cited crop growth stages and your recorded planting date.",
                "Alerts use genuine forecasts and the local season; they are not a substitute for field inspection.",
            ],
        },
        sources=[],
        retrieved_at=iso_now(),
    )

    # ---- Recommendations --------------------------------------------------
    rec_context = {
        "today": today,
        "zone": zone,
        "soil": sections["soil"].as_dict(),
        "weather": sections["weather"].as_dict(),
        "rainfall": sections["rainfall"].as_dict(),
        "vegetation": sections["vegetation"].as_dict(),
        "terrain": sections["terrain"].as_dict(),
        "landcover": sections["landcover"].as_dict(),
        "wapor": sections["water"].as_dict(),
        "selected_crop": selected_eval,
        "measured_soil": measured or None,
        "irrigation": irrigation,
        "advisories": farm.get("advisories") or [],
    }
    recs = engine_recs.build_recommendations(rec_context)
    sections["recommendations"] = Section(
        key="recommendations",
        status="ok",
        data=recs,
        sources=[],
        retrieved_at=iso_now(),
    )
    steps_done += 1
    if progress:
        progress(0.98, "Assembling report", None)

    payload = {
        "analysis_id": uuid.uuid4().hex,
        "version": ANALYSIS_VERSION,
        "generated_at": iso_now(),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "farm": {
            "id": farm["id"],
            "name": farm["name"],
            "geometry": info.geometry,
            "area_ha": info.area_ha,
            "perimeter_m": info.perimeter_m,
            "centroid": {"lat": info.centroid_lat, "lon": info.centroid_lon},
            "bbox": info.bbox,
            "vertex_count": info.vertex_count,
            "geometry_label": geometry_label,
            "field_id": field_id,
            "is_demo": bool(farm.get("is_demo")),
            "source": farm.get("source"),
            "crop_id": crop_id,
            "planting_date": planting.isoformat() if planting else None,
            "irrigation": irrigation,
            "management_level": management_level,
            "district": location.get("district") or farm.get("district"),
            "region": location.get("region") or farm.get("region"),
            "zone": zone,
            "annual_rainfall_mm": annual.get("annual_mm"),
            "location_match": location.get("match"),
        },
        "sections": {key: section.as_dict() for key, section in sections.items()},
        "summary": {
            "top_actions": recs["top_actions"],
            "counts": recs["counts"],
            "data_status": _data_status(sections),
            "headline": _headline(sections, selected_eval, crop_for_calendar),
        },
        "provenance": _provenance(sections),
        "sources_registry": _source_registry(),
        "disclaimer": (
            "Remote sensing and modelled data cannot replace soil testing, field inspection or extension advice. "
            "Farm Intelligence does not infer land ownership and does not make definitive pest, disease, "
            "fertiliser or yield claims from imagery."
        ),
    }
    return payload


def _headline(sections: dict[str, Section], selected: dict[str, Any] | None, crop: dict[str, Any] | None) -> dict[str, Any]:
    soil = sections["soil"]
    weather = sections["weather"]
    vegetation = sections["vegetation"]
    rainfall = sections["rainfall"]
    measured = (soil.data.get("measured") or {}).get("ph")
    ph = measured or ((soil.data.get("modelled") or {}).get("profile_0_30") or {}).get("phh2o", {}).get("value")
    current = weather.data.get("current") or {}
    ndvi = ((vegetation.data.get("latest") or {}).get("ndvi") or {}).get("mean")
    anomaly = (rainfall.data.get("anomalies") or {}).get("last_30_days", {}).get("percent_of_normal")
    return {
        "crop": (selected or {}).get("name") or (crop or {}).get("name"),
        "crop_suitability": (selected or {}).get("category_label"),
        "soil_ph": ph,
        "temperature_c": current.get("temperature_c"),
        "weather": current.get("weather"),
        "ndvi": ndvi,
        "rainfall_percent_of_normal_30d": anomaly,
    }


def _failed_section(key: str, exc: Exception) -> Section:
    log.warning("Analysis section %s failed: %s", key, exc)
    return Section(
        key=key,
        status="error",
        data={"error": {"code": "section_failed", "message": str(exc)[:300]}},
        sources=[],
        message="This section could not be computed. No value is shown rather than an estimate.",
    )


# --------------------------------------------------------------------------- #
# Cache + jobs
# --------------------------------------------------------------------------- #
def _inputs_fingerprint(farm: dict[str, Any]) -> str:
    """Fingerprint the farm-attached inputs that change guidance but not geometry.

    A recorded laboratory test or a new advisory must invalidate a cached
    analysis; otherwise "record the results here so advice switches to
    measurements" is false for the whole cache TTL.
    """
    tests = farm.get("soil_tests") or []
    latest = tests[0] if tests else {}
    advisories = farm.get("advisories") or []
    return stable_hash(
        [
            latest.get("id"),
            latest.get("sampled_on"),
            latest.get("ph"),
            latest.get("soc_gkg"),
            latest.get("texture_class"),
            sorted(str(advisory.get("id")) for advisory in advisories),
        ]
    )


def analysis_key(farm: dict[str, Any], field_id: str | None, crop_id: str | None, planting_date: str | None,
                 management_level: str) -> str:
    day = datetime.now(GH_TZ).date().isoformat()
    geometry = farm["geometry"] if not field_id else next(
        (field["geometry"] for field in farm["fields"] if field["id"] == field_id), farm["geometry"]
    )
    return "analysis:" + stable_hash(
        [
            farm["id"],
            geometry,
            field_id,
            crop_id,
            planting_date,
            management_level,
            farm.get("irrigation"),
            _inputs_fingerprint(farm),
            day,
            ANALYSIS_VERSION,
        ]
    )


def get_cached(key: str) -> dict[str, Any] | None:
    entry = analysis_cache_get(key)
    if entry is None or entry["expired"]:
        return None
    payload = entry["payload"]
    payload["cached"] = True
    payload["cached_at"] = entry["created_at"]
    return payload


def run_and_cache(farm: dict[str, Any], key: str, **kwargs) -> dict[str, Any]:
    payload = build_analysis(farm, **kwargs)
    payload["cached"] = False
    analysis_cache_put(key, farm["id"], payload, settings.analysis_cache_ttl_s)
    return payload


def _progress_summary(partial: dict[str, Any]) -> dict[str, Any]:
    """What a progress write stores: section statuses, not the whole payload.

    The full payload is written once, at completion. Rewriting every section at
    every step stored hundreds of kilobytes per job per step, and the client only
    reads the statuses while it waits.
    """
    sections = partial.get("sections") or {}
    return {
        "partial": True,
        "sections": {
            key: {"key": key, "status": section.get("status"), "message": section.get("message")}
            for key, section in sections.items()
        },
    }


def submit_job(farm_id: str, params: dict[str, Any], *, force: bool = False) -> str:
    job_id = uuid.uuid4().hex
    job_create(job_id, farm_id, "analysis")
    job_update(job_id, status="running", progress=0.02, message="Queued")

    def work() -> None:
        try:
            farm = load_farm(farm_id)
            key = analysis_key(
                farm,
                params.get("field_id"),
                params.get("crop_id") or farm.get("crop_id"),
                params.get("planting_date") or farm.get("planting_date"),
                params.get("management_level", "medium"),
            )
            # `force` must survive into the job body: checking the cache here
            # regardless of force made the UI's refresh a no-op.
            cached = None if force else get_cached(key)
            if cached:
                job_update(job_id, status="complete", progress=1.0, message="Loaded from cache", result=cached)
                return

            def progress(value: float, message: str, partial: dict[str, Any] | None) -> None:
                if partial:
                    job_update(job_id, progress=round(value, 3), message=message, result=_progress_summary(partial))
                else:
                    job_update(job_id, progress=round(value, 3), message=message)

            payload = run_and_cache(
                farm,
                key,
                field_id=params.get("field_id"),
                crop_id=params.get("crop_id"),
                planting_date=params.get("planting_date"),
                management_level=params.get("management_level", "medium"),
                progress=progress,
            )
            job_update(job_id, status="complete", progress=1.0, message="Analysis complete", result=payload)
        except GeometryError as exc:
            job_update(job_id, status="failed", message=exc.message, error=json.dumps(exc.as_dict()))
        except KeyError:
            job_update(job_id, status="failed", message="Farm not found", error="farm_not_found")
        except Exception as exc:  # noqa: BLE001
            log.exception("Analysis job failed")
            job_update(job_id, status="failed", message="Analysis failed", error=str(exc)[:500])

    _EXECUTOR.submit(work)
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    return job_get(job_id)
