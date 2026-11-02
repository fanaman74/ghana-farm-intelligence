"""CHIRPS v2.0 rainfall through the Digital Earth Africa public COG mirror.

* Daily and monthly COGs are read with GDAL range requests; only the farm window
  is fetched.
* The 30/90-day totals always end on the latest day CHIRPS has published — never
  on "today" if data is lagging — and each window reports `complete: false`, its
  covered day count and the missing days when a raster in the window was absent.
  An incomplete window is compared with the same number of baseline days.
* The anomaly baseline is a bundled 1991–2020 Ghana climatology derived from the
  same monthly product.
"""

from __future__ import annotations

import calendar
import gzip
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..config import GH_TZ, SEED_DIR
from ..db import cache_get, cache_put
from ..geo.geodesy import ensure_shapely, polygon_hash
from ..geo.raster import open_raster, read_polygon
from ..utils import iso_now
from .base import ProviderError, Section, Source, STATUS_OK, cached, client, request
from .registry import provider_map

log = logging.getLogger(__name__)

PROVIDER = "chirps"
S3_BASE = "https://deafrica-input-datasets.s3.af-south-1.amazonaws.com/"
DAILY_KEY = "rainfall_chirps_daily/{year}/{month:02d}/chirps-v2.0_{year}.{month:02d}.{day:02d}.tif"
MONTHLY_KEY = "rainfall_chirps_monthly/chirps-v2.0_{year}.{month:02d}.tif"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
CLIMATOLOGY = SEED_DIR / "chirps" / "ghana_climatology_1991_2020.tif"
CLIM_START, CLIM_END = 1991, 2020


def daily_url(day: date) -> str:
    return S3_BASE + DAILY_KEY.format(year=day.year, month=day.month, day=day.day)


def monthly_url(year: int, month: int) -> str:
    return S3_BASE + MONTHLY_KEY.format(year=year, month=month)


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
def _list_month(year: int, month: int) -> list[str]:
    response = client().get(
        S3_BASE,
        params={"list-type": "2", "prefix": f"rainfall_chirps_daily/{year}/{month:02d}/", "max-keys": 1000},
    )
    if response.status_code != 200:
        raise ProviderError(PROVIDER, "s3_listing", f"S3 listing returned HTTP {response.status_code}")
    root = ET.fromstring(response.text)
    return sorted(
        node.find(NS + "Key").text
        for node in root.findall(NS + "Contents")
        if node.find(NS + "Key") is not None and node.find(NS + "Key").text.endswith(".tif")
    )


def latest_available() -> date:
    """Newest published CHIRPS day, probed from the S3 listing (cached 6 h)."""

    def fetch() -> dict[str, str]:
        today = datetime.now(GH_TZ).date()
        cursor = today.replace(day=1)
        for _ in range(4):
            keys = _list_month(cursor.year, cursor.month)
            if keys:
                filename = keys[-1].rsplit("/", 1)[-1]
                stamp = filename.replace("chirps-v2.0_", "").replace(".tif", "")
                year, month, day = (int(part) for part in stamp.split("."))
                return {"latest": date(year, month, day).isoformat()}
            cursor = (cursor - timedelta(days=1)).replace(day=1)
        raise ProviderError(PROVIDER, "no_data", "No CHIRPS daily files were listed for recent months")

    payload, meta = cached(["chirps", "latest"], PROVIDER, 6 * 3600, fetch)
    return date.fromisoformat(payload["latest"])


# --------------------------------------------------------------------------- #
# Raster sampling
# --------------------------------------------------------------------------- #
def _read_window_mean(url: str, geom) -> tuple[float | None, dict[str, Any]]:
    with open_raster(url) as ds:
        rw = read_polygon(ds, geom)
        values = rw.data[0]
        nodata = rw.nodata if rw.nodata is not None else -9999.0
        valid = rw.mask & np.isfinite(values) & (values != nodata) & (values > -9000)
        if not valid.any():
            return None, {"valid_fraction": 0.0, "pixels": int(rw.mask.sum())}
        mean = float(np.average(values[valid], weights=rw.weights[valid]))
        total = rw.weights[rw.mask].sum()
        fraction = float(rw.weights[valid & rw.mask].sum() / total) if total else 0.0
        return mean, {
            "valid_fraction": round(fraction, 4),
            "pixels": int(valid.sum()),
            "resolution_m": round(max(rw.res_x, rw.res_y) * 111_320, 0),
        }


def daily_value(day: date, geom) -> dict[str, Any]:
    geom_hash = polygon_hash(geom)
    cache_key = f"{PROVIDER}:daily:{geom_hash}:{day.isoformat()}"

    def fetch() -> dict[str, Any]:
        mean, meta = _read_window_mean(daily_url(day), geom)
        return {"date": day.isoformat(), "mm": mean, **meta}

    entry = cache_get(cache_key)
    if entry and not entry["expired"]:
        return entry["payload"]
    try:
        value = fetch()
    except Exception as exc:  # noqa: BLE001
        if entry is not None:
            return entry["payload"]
        raise ProviderError(PROVIDER, "daily_read", f"CHIRPS daily raster for {day} failed: {exc}") from exc
    cache_put(cache_key, PROVIDER, value, 365 * 24 * 3600)
    return value


def monthly_value(year: int, month: int, geom) -> dict[str, Any]:
    geom_hash = polygon_hash(geom)
    cache_key = f"{PROVIDER}:monthly:{geom_hash}:{year}-{month:02d}"

    def fetch() -> dict[str, Any]:
        mean, meta = _read_window_mean(monthly_url(year, month), geom)
        return {"year": year, "month": month, "mm": mean, **meta}

    entry = cache_get(cache_key)
    if entry and not entry["expired"]:
        return entry["payload"]
    try:
        value = fetch()
    except Exception as exc:  # noqa: BLE001
        if entry is not None:
            return entry["payload"]
        raise ProviderError(PROVIDER, "monthly_read", f"CHIRPS monthly raster {year}-{month} failed: {exc}") from exc
    cache_put(cache_key, PROVIDER, value, 365 * 24 * 3600)
    return value


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return months


def _safe_daily(day: date, geom) -> dict[str, Any]:
    """Daily value, or an explicit null when the raster is unavailable."""
    try:
        return daily_value(day, geom)
    except ProviderError as exc:
        log.warning("CHIRPS daily %s unavailable: %s", day, exc)
        return {"date": day.isoformat(), "mm": None}


def _safe_monthly(year: int, month: int, geom) -> dict[str, Any]:
    try:
        return monthly_value(year, month, geom)
    except ProviderError as exc:
        log.warning("CHIRPS monthly %s-%02d unavailable: %s", year, month, exc)
        return {"year": year, "month": month, "mm": None}


def _window_total(start: date, end: date, geom) -> dict[str, Any]:
    """Total for [start, end], stating openly what was missing.

    Whole months use the monthly product, partial months the daily product. A
    missing raster is recorded, never counted as zero rain: a partial sum
    presented as a total reads as drought.
    """
    total = 0.0
    days_covered = 0
    months: list[dict[str, Any]] = []
    missing: list[str] = []
    sources_used: set[str] = set()
    for year, month in _months_between(start, end):
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        overlap_start = max(start, month_start)
        overlap_end = min(end, month_end)
        if overlap_start > overlap_end:
            continue
        if overlap_start == month_start and overlap_end == month_end:
            value = _safe_monthly(year, month, geom)
            if value.get("mm") is None:
                missing.append(f"{year}-{month:02d}")
                months.append({"year": year, "month": month, "mm": None, "mode": "monthly raster"})
                continue
            total += float(value["mm"])
            days_covered += (month_end - month_start).days + 1
            months.append({"year": year, "month": month, "mm": round(float(value["mm"]), 1), "mode": "monthly raster"})
            sources_used.add("monthly")
        else:
            days = [overlap_start + timedelta(days=i) for i in range((overlap_end - overlap_start).days + 1)]
            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(lambda d: _safe_daily(d, geom), days))
            subtotal = sum(float(r["mm"]) for r in results if r.get("mm") is not None)
            counted = sum(1 for r in results if r.get("mm") is not None)
            total += subtotal
            days_covered += counted
            missing.extend(r["date"] for r in results if r.get("mm") is None)
            months.append(
                {
                    "year": year,
                    "month": month,
                    "mm": round(subtotal, 1) if counted else None,
                    "mode": "daily rasters",
                    "days": counted,
                }
            )
            sources_used.add("daily")
    expected = (end - start).days + 1
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_mm": round(total, 1) if days_covered else None,
        "days": days_covered,
        "expected_days": expected,
        "missing_days": len(missing),
        "missing": missing[:40],
        "complete": days_covered == expected,
        "months": months,
        "sources_used": sorted(sources_used),
    }


# --------------------------------------------------------------------------- #
# Climatology
# --------------------------------------------------------------------------- #
def climatology(geom) -> dict[str, Any] | None:
    """Monthly 1991–2020 means for the polygon (from the bundled Ghana raster)."""
    if CLIMATOLOGY.exists():
        with open_raster(CLIMATOLOGY) as ds:
            rw = read_polygon(ds, geom, bands=tuple(range(1, 13)))
            values = rw.data
            nodata = rw.nodata if rw.nodata is not None else -9999.0
            monthly = {}
            for index in range(12):
                layer = values[index]
                valid = rw.mask & np.isfinite(layer) & (layer != nodata) & (layer > -9000)
                if not valid.any():
                    monthly[str(index + 1)] = None
                    continue
                monthly[str(index + 1)] = round(
                    float(np.average(layer[valid], weights=rw.weights[valid])), 1
                )
            return {
                "monthly_mean_mm": monthly,
                "annual_total_mm": round(sum(v for v in monthly.values() if v is not None), 1),
                "period": f"{CLIM_START}–{CLIM_END}",
                "source": "bundled from CHIRPS v2.0 monthly rasters",
            }
    return None


def _baseline_total(
    start: date, end: date, clim: dict[str, Any], days_covered: int | None = None
) -> float | None:
    monthly = clim.get("monthly_mean_mm") if clim else None
    if not monthly:
        return None
    total = 0.0
    for year, month in _months_between(start, end):
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        overlap_start = max(start, month_start)
        overlap_end = min(end, month_end)
        if overlap_start > overlap_end:
            continue
        month_total = monthly.get(str(month))
        if month_total is None:
            return None
        share = ((overlap_end - overlap_start).days + 1) / ((month_end - month_start).days + 1)
        total += month_total * share
    expected = (end - start).days + 1
    if days_covered is not None and 0 < days_covered < expected:
        # Compare like with like: an incomplete observed record is measured against
        # the same share of the baseline instead of against the full window, which
        # would always look like a deficit.
        total *= days_covered / expected
    return total


def _anomaly_label(percent: float | None) -> str:
    if percent is None:
        return "No baseline"
    if percent < 50:
        return "Much below normal"
    if percent < 80:
        return "Below normal"
    if percent <= 120:
        return "Near normal"
    if percent <= 150:
        return "Above normal"
    return "Much above normal"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def annual_rainfall(geom) -> dict[str, Any] | None:
    clim = climatology(geom)
    if not clim:
        return None
    return {
        "annual_mm": clim["annual_total_mm"],
        "monthly_mm": clim["monthly_mean_mm"],
        "period": clim["period"],
    }


def analyse(geom) -> Section:
    geom = ensure_shapely(geom)
    try:
        latest = latest_available()
    except ProviderError:
        raise

    today = datetime.now(GH_TZ).date()
    lag_days = max(0, (today - latest).days)
    warnings: list[str] = []
    if lag_days > 7:
        warnings.append(
            f"CHIRPS rainfall is published to {latest:%d %b %Y}, {lag_days} days before today. "
            "Totals below end on that date, not today."
        )

    end = latest
    windows = {
        "last_7_days": (end - timedelta(days=6), end),
        "last_30_days": (end - timedelta(days=29), end),
        "last_90_days": (end - timedelta(days=89), end),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        window_results = list(pool.map(lambda item: _window_total(item[1][0], item[1][1], geom), windows.items()))
    totals = {name: result for name, result in zip(windows.keys(), window_results)}

    # Daily series for the most recent 30 days (also powers the timeline).
    daily_days = [end - timedelta(days=i) for i in range(29, -1, -1)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        daily = list(pool.map(lambda day: _safe_daily(day, geom), daily_days))
    daily_series = [
        {
            "date": row["date"],
            "mm": round(float(row["mm"]), 1) if row.get("mm") is not None else None,
        }
        for row in daily
    ]
    values = [row["mm"] for row in daily_series if row["mm"] is not None]
    rainy_days = sum(1 for value in values if value >= 1.0)
    heavy_days = sum(1 for value in values if value >= 25.0)

    longest_dry = current = 0
    for value in values:
        if value < 1.0:
            current += 1
            longest_dry = max(longest_dry, current)
        else:
            current = 0

    clim = climatology(geom)
    anomalies = {}
    for name, (start, stop) in windows.items():
        window = totals[name]
        observed = window.get("total_mm")
        baseline = (
            _baseline_total(start, stop, clim, days_covered=window.get("days")) if clim else None
        )
        if observed is None:
            anomalies[name] = {
                "observed_mm": None,
                "baseline_mm": None,
                "percent_of_normal": None,
                "label": "No daily or monthly rainfall raster was available for this window",
                "complete": False,
                "days_covered": 0,
            }
            continue
        if baseline is not None and baseline > 0:
            percent = observed / baseline * 100
            anomaly = {
                "observed_mm": observed,
                "baseline_mm": round(baseline, 1),
                "anomaly_mm": round(observed - baseline, 1),
                "percent_of_normal": round(percent, 0),
                "label": _anomaly_label(percent),
                "baseline_period": clim["period"] if clim else None,
                "complete": bool(window.get("complete")),
                "days_covered": window.get("days"),
                "expected_days": window.get("expected_days"),
            }
            if not window.get("complete"):
                anomaly["label"] = f"{anomaly['label']} (partial record: {window.get('days')} of {window.get('expected_days')} days)"
            anomalies[name] = anomaly
        else:
            anomalies[name] = {
                "observed_mm": observed,
                "baseline_mm": None,
                "percent_of_normal": None,
                "label": "No baseline available",
                "complete": bool(window.get("complete")),
                "days_covered": window.get("days"),
            }

    incomplete = [
        f"{name.replace('last_', '').replace('_days', '-day')} ({totals[name]['days']}/{totals[name]['expected_days']} days)"
        for name in totals
        if not totals[name].get("complete")
    ]
    if incomplete:
        warnings.append(
            "Some CHIRPS rasters were missing, so these totals cover fewer days than requested: "
            + ", ".join(incomplete)
            + ". The 1991–2020 baseline is scaled to the same number of days; the anomaly is marked partial."
        )

    monthly_series = []
    months_window = _months_between(end - timedelta(days=364), end)[:13]

    def read_month(item: tuple[int, int]) -> dict[str, Any]:
        year, month = item
        try:
            value = monthly_value(year, month, geom)
            return {"year": year, "month": month, "mm": round(float(value["mm"]), 1) if value.get("mm") is not None else None}
        except ProviderError:
            return {"year": year, "month": month, "mm": None}

    with ThreadPoolExecutor(max_workers=6) as pool:
        monthly_series = list(pool.map(read_month, months_window))
    monthly_series.sort(key=lambda item: (item["year"], item["month"]))

    sources = [
        Source(
            provider_id=PROVIDER,
            dataset="CHIRPS v2.0 daily and monthly rainfall (Digital Earth Africa mirror)",
            url="https://docs.digitalearthafrica.org/en/latest/data_specs/CHIRPS_specs.html",
            licence="CHIRPS: public domain (UCSB CHC); DE Africa mirror: CC BY 4.0",
            resolution="0.05° (~5.5 km)",
            period=f"{windows['last_90_days'][0]:%Y-%m-%d} to {latest:%Y-%m-%d} (latest available)",
            method="COG range reads of the farm window; polygon mask; area-weighted means. Whole months use the monthly product, partial months the daily product.",
            limitations="Satellite/station blended estimate at ~5.5 km, not a rain gauge on the farm. Publication lags the calendar date.",
            access_method="Public S3 HTTPS COGs (no key)",
            key_required=False,
        )
    ]
    if clim:
        sources.append(
            Source(
                provider_id=PROVIDER,
                dataset=f"CHIRPS {CLIM_START}–{CLIM_END} monthly climatology (Ghana subset)",
                url="https://data.chc.ucsb.edu/products/CHIRPS-2.0/",
                licence="CHIRPS: public domain (UCSB CHC)",
                resolution="0.05° (~5.5 km)",
                period=f"{CLIM_START}–{CLIM_END}",
                method="Mean of monthly rasters per calendar month, computed for this deployment",
                limitations="A 30-year average does not describe this year's season.",
                access_method="Bundled derived raster",
                key_required=False,
            )
        )

    return Section(
        key="rainfall",
        status=STATUS_OK,
        data={
            "latest_available": latest.isoformat(),
            "lag_days": lag_days,
            "today": today.isoformat(),
            "windows": windows and {name: totals[name] for name in totals},
            "anomalies": anomalies,
            "daily_series": daily_series,
            "monthly_series": monthly_series,
            "recent_30_days": {
                "rainy_days": rainy_days,
                "heavy_rain_days": heavy_days,
                "longest_dry_spell_days": longest_dry,
                "days": len(values),
            },
            "climatology": clim,
            "source_kind": "observation_estimate",
            "notes": [
                "Totals are for the dates shown — the latest CHIRPS day, not today.",
                "Anomaly compares the same calendar window with the 1991–2020 mean for this polygon.",
            ],
        },
        sources=sources,
        warnings=warnings,
        retrieved_at=iso_now(),
    )
