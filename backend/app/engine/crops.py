"""Ghana crop reference access and season/calendar helpers."""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from ..config import BUNDLED_DATA_DIR

KB_PATH = BUNDLED_DATA_DIR / "ghana_crops.json"

# The knowledge base uses five zone keys; the app's rainfall classifier splits
# forest into deciduous/rain forest.
ZONE_TO_KB = {
    "sudan_savannah": "sudan_savannah",
    "guinea_savannah": "guinea_savannah",
    "transition": "transition",
    "deciduous_forest": "forest",
    "rain_forest": "forest",
    "coastal_savannah": "coastal_savannah",
}

SEASON_LABELS = {
    "south_bimodal": {"major": "Major season", "minor": "Minor season"},
    "north_unimodal": {"single": "Rainy season"},
}


@lru_cache(maxsize=1)
def load_kb() -> dict[str, Any]:
    if not KB_PATH.exists():
        return {"crops": [], "sources": {}, "agro_ecological_zones": {}}
    return json.loads(KB_PATH.read_text(encoding="utf-8"))


def sources_for(source_ids: list[str]) -> list[dict[str, Any]]:
    kb = load_kb()
    output = []
    for source_id in source_ids or []:
        source = kb.get("sources", {}).get(source_id)
        if source:
            output.append(
                {
                    "id": source_id,
                    "title": source.get("title"),
                    "publisher": source.get("publisher"),
                    "url": source.get("url"),
                    "licence": source.get("licence"),
                    "accessed": source.get("accessed"),
                    "notes": source.get("notes"),
                }
            )
    return output


def all_crops() -> list[dict[str, Any]]:
    return load_kb().get("crops", [])


def get_crop(crop_id: str | None) -> dict[str, Any] | None:
    if not crop_id:
        return None
    for crop in all_crops():
        if crop["id"] == crop_id:
            return crop
    return None


def crop_summaries() -> list[dict[str, Any]]:
    summaries = []
    for crop in all_crops():
        summaries.append(
            {
                "id": crop["id"],
                "name": crop["name"],
                "scientific_name": crop.get("scientific_name"),
                "local_names": crop.get("local_names", {}),
                "perennial": bool(crop.get("perennial")),
                "cycle_days": crop.get("cycle_days"),
                "water": {
                    "seasonal_rainfall_mm": crop.get("water", {}).get("seasonal_rainfall_mm"),
                    "drought_tolerance": crop.get("water", {}).get("drought_tolerance"),
                },
                "soil": {
                    "ph": crop.get("soil", {}).get("ph"),
                    "ph_optimal": crop.get("soil", {}).get("ph_optimal"),
                    "texture_preferred": crop.get("soil", {}).get("texture_preferred"),
                },
                "temperature_c": crop.get("temperature_c"),
                "slope_max_percent": crop.get("slope_max_percent"),
                "zones": crop.get("agro_ecological_suitability"),
                "sources": sources_for(crop.get("source_ids", [])),
                "limitations": crop.get("limitations"),
                "common_stresses": crop.get("common_stresses", []),
                "evidence_quality": crop.get("evidence_quality"),
            }
        )
    return summaries


def season_set_for_zone(zone_id: str | None) -> str:
    if zone_id in ("sudan_savannah", "guinea_savannah"):
        return "north_unimodal"
    return "south_bimodal"


def season_windows(crop: dict[str, Any], zone_id: str | None) -> list[dict[str, Any]]:
    """Planting/harvest windows for the zone's season set."""
    seasons = crop.get("seasons") or {}
    set_key = season_set_for_zone(zone_id)
    block = seasons.get(set_key) or {}
    output = []
    for season_key, window in block.items():
        if not isinstance(window, dict):
            continue
        planting = window.get("planting_window")
        harvest = window.get("harvest_window")
        if not planting or not harvest:
            continue
        output.append(
            {
                "season": season_key,
                "label": SEASON_LABELS.get(set_key, {}).get(season_key, season_key.title()),
                "planting_window": _window_dates(planting),
                "harvest_window": _window_dates(harvest),
            }
        )
    return sorted(output, key=lambda item: item["planting_window"]["start_month_day"])


def _window_dates(window: list[list[int]]) -> dict[str, Any]:
    (sm, sd), (em, ed) = window
    return {
        "start": f"{sm:02d}-{sd:02d}",
        "end": f"{em:02d}-{ed:02d}",
        "start_month_day": (sm, sd),
        "end_month_day": (em, ed),
        "label": f"{_month_name(sm)} {sd} – {_month_name(em)} {ed}",
    }


MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _month_name(month: int) -> str:
    return MONTHS[(month - 1) % 12]


def _month_day_in_year(year: int, month: int, day: int) -> date:
    day = min(day, monthrange(year, month)[1])
    return date(year, month, day)


def date_for_window(month_day: tuple[int, int], year: int) -> date:
    """Public helper: resolve a (month, day) window edge for a given year."""
    return _month_day_in_year(year, month_day[0], month_day[1])


def season_status(
    crop: dict[str, Any], zone_id: str | None, today: date
) -> dict[str, Any] | None:
    """Which season applies now, and whether the planting window is open."""
    windows = season_windows(crop, zone_id)
    if not windows:
        return None
    best = None
    for window in windows:
        start = _month_day_in_year(today.year, *window["planting_window"]["start_month_day"])
        end = _month_day_in_year(today.year, *window["planting_window"]["end_month_day"])
        if start <= today <= end:
            state = "open"
            days = (end - today).days
        elif today < start:
            state = "upcoming"
            days = (start - today).days
        else:
            state = "passed"
            days = None
        candidate = {**window, "state": state, "days_to_window": days,
                     "start_iso": start.isoformat(), "end_iso": end.isoformat()}
        if state == "open":
            best = candidate
            break
        if best is None or (state == "upcoming" and (best.get("days_to_window") or 9999) > (days or 9999)):
            best = candidate
    return best


def stages_with_dates(crop: dict[str, Any], planting_date: date | None) -> list[dict[str, Any]]:
    stages = crop.get("growth_stages") or []
    output = []
    for stage in stages:
        entry = {
            "name": stage.get("name"),
            "start_day": stage.get("start_day"),
            "end_day": stage.get("end_day"),
            "notes": stage.get("notes"),
            "start_date": None,
            "end_date": None,
        }
        if planting_date:
            entry["start_date"] = (planting_date + timedelta(days=int(stage.get("start_day") or 0))).isoformat()
            entry["end_date"] = (planting_date + timedelta(days=int(stage.get("end_day") or 0))).isoformat()
        output.append(entry)
    return output


def current_stage(
    crop: dict[str, Any], planting_date: date | None, today: date
) -> dict[str, Any] | None:
    if not planting_date:
        return None
    days = (today - planting_date).days
    for stage in crop.get("growth_stages") or []:
        if int(stage.get("start_day") or 0) <= days <= int(stage.get("end_day") or 0):
            return {
                "name": stage.get("name"),
                "days_since_planting": days,
                "start_day": stage.get("start_day"),
                "end_day": stage.get("end_day"),
                "notes": stage.get("notes"),
            }
    return {
        "name": "Between recorded stages" if days >= 0 else "Before planting",
        "days_since_planting": days,
        "start_day": None,
        "end_day": None,
        "notes": None,
    }


def seasonal_rainfall_from_climatology(
    monthly_mm: dict[str, float] | None, start: date, days: int
) -> float | None:
    """Sum monthly climatology over a planting window of ``days`` days."""
    if not monthly_mm:
        return None
    total = 0.0
    counted = 0
    cursor = start
    end = start + timedelta(days=days - 1)
    while cursor <= end:
        month_total = monthly_mm.get(str(cursor.month))
        if month_total is None:
            return None
        days_in_month = monthrange(cursor.year, cursor.month)[1]
        month_end = date(cursor.year, cursor.month, days_in_month)
        overlap_end = min(end, month_end)
        overlap_days = (overlap_end - cursor).days + 1
        total += float(month_total) * overlap_days / days_in_month
        counted += overlap_days
        cursor = overlap_end + timedelta(days=1)
    return round(total, 1) if counted else None


def mean_temperature_from_normals(
    monthly_c: dict[str, float] | None, start: date, days: int
) -> float | None:
    if not monthly_c:
        return None
    values = []
    cursor = start
    end = start + timedelta(days=days - 1)
    while cursor <= end:
        month_value = monthly_c.get(str(cursor.month))
        if month_value is not None:
            values.append(float(month_value))
        cursor = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
    return round(sum(values) / len(values), 2) if values else None
