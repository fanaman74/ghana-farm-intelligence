"""ISRIC SoilGrids 2.0 for the selected farm polygon.

Access path
-----------
SoilGrids' REST API is reported as paused, and its WCS GetCoverage subsets over
Ghana contained periodic zero artefacts when tested (2026-10-31). The adapter
therefore uses ISRIC's documented WebDAV/VRT rasters to build a compact,
Ghana-wide, 250 m subset once (``scripts/seed_soilgrids.py`` or on demand), then
samples that local grid with an exact polygon mask and area weights.

SoilGrids values are predictions. Farmer-entered laboratory results take
precedence for guidance and are shown separately.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np

from ..db import cache_get, cache_put
from ..geo.geodesy import ensure_shapely
from ..geo.raster import (
    describe_resolution,
    read_polygon,
    sub_field_detail_supported,
    summarise,
    valid_fraction,
)
from ..utils import iso_now, round_sig, stable_hash
from .base import ProviderError, Section, Source, STATUS_OK, STATUS_STALE
from .soilgrids_layers import open_layer

log = logging.getLogger(__name__)

PROVIDER = "soilgrids"

PROPERTIES: dict[str, dict[str, Any]] = {
    # Scales follow ISRIC's documented conversion factors (raw value / factor = conventional unit).
    "phh2o": {"label": "pH (in water)", "unit": "pH", "scale": 10.0, "decimals": 2, "range": (2.5, 11.0)},
    "soc": {"label": "Soil organic carbon", "unit": "g/kg", "scale": 10.0, "decimals": 1, "range": (0.0, 300.0)},
    "nitrogen": {"label": "Total nitrogen", "unit": "g/kg", "scale": 100.0, "decimals": 2, "range": (0.0, 50.0)},
    "clay": {"label": "Clay", "unit": "%", "scale": 10.0, "decimals": 1, "range": (0.0, 100.0)},
    "sand": {"label": "Sand", "unit": "%", "scale": 10.0, "decimals": 1, "range": (0.0, 100.0)},
    "silt": {"label": "Silt", "unit": "%", "scale": 10.0, "decimals": 1, "range": (0.0, 100.0)},
    "cec": {
        "label": "Cation exchange capacity",
        "unit": "cmol(c)/kg",
        "scale": 10.0,
        "decimals": 2,
        "range": (0.0, 100.0),
    },
    "bdod": {"label": "Bulk density", "unit": "kg/dm³", "scale": 100.0, "decimals": 2, "range": (0.2, 2.5)},
    "cfvo": {"label": "Coarse fragments", "unit": "vol %", "scale": 100.0, "decimals": 2, "range": (0.0, 100.0)},
}

DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]
DEPTH_CM = {"0-5cm": (0, 5), "5-15cm": (5, 15), "15-30cm": (15, 30)}
DEPTH_WEIGHT = {"0-5cm": 5.0, "5-15cm": 10.0, "15-30cm": 15.0}
UNCERTAINTY_DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]


def _layer_stats(prop: str, depth: str, stat: str, geom) -> tuple[dict[str, Any], str]:
    """Sample one SoilGrids layer over the polygon, cached per polygon."""
    spec = PROPERTIES[prop]
    geom_hash = stable_hash([round(v, 6) for v in geom.bounds])
    cache_key = f"{PROVIDER}:poly:{geom_hash}:{prop}:{depth}:{stat}"
    entry = cache_get(cache_key)
    if entry and not entry["expired"]:
        return entry["payload"], "cache"

    with open_layer(prop, depth, stat) as dataset:
        rw = read_polygon(dataset, geom)
    values = rw.data[0]
    nodata = np.float64(rw.nodata) if rw.nodata is not None else np.float64(-32768)
    finite = np.isfinite(values) & (values != nodata) & (values != 0)
    scaled = values / spec["scale"]
    low, high = spec["range"]
    valid = finite & (scaled >= low) & (scaled <= high)

    stats = summarise(values, valid, rw.weights, unit_scale=1.0 / spec["scale"], decimals=spec["decimals"])
    stats["valid_fraction"] = round(valid_fraction(rw, valid), 4)
    stats["resolution"] = describe_resolution(rw)
    stats["sub_field_detail"] = sub_field_detail_supported(rw.polygon_area_m2, rw.pixel_area_m2)
    stats["coverage_fraction"] = round(rw.coverage_fraction, 4)
    stats["unit"] = spec["unit"]
    stats["pixels_in_polygon"] = int(rw.mask.sum())
    stats["pixel_area_ha"] = round(rw.pixel_area_m2 / 10_000, 3)
    stats["single_grid_cell"] = stats["pixels_in_polygon"] <= 1
    cache_put(cache_key, PROVIDER, stats, 365 * 24 * 3600)
    return stats, "live"


def usda_texture_class(sand: float | None, clay: float | None, silt: float | None) -> str | None:
    """Approximate USDA texture class from estimated mass fractions (12 classes)."""
    if None in (sand, clay, silt):
        return None
    total = float(sand) + float(clay) + float(silt)
    if total <= 0:
        return None
    s, c, si = sand / total * 100, clay / total * 100, silt / total * 100
    if si >= 80 and c < 12:
        return "Silt"
    if c >= 40 and s <= 45 and si <= 40:
        return "Clay"
    if 40 <= c < 60 and si >= 40:
        return "Silty clay"
    if 35 <= c < 55 and s >= 45:
        return "Sandy clay"
    if 27 <= c < 40 and si >= 40:
        return "Silty clay loam"
    if 27 <= c < 40 and 20 <= s < 45:
        return "Clay loam"
    if 20 <= c < 35 and s >= 45 and si < 28:
        return "Sandy clay loam"
    if si >= 50 and 12 <= c < 27:
        return "Silt loam"
    if 7 <= c < 27 and 28 <= si < 50 and 23 <= s < 52:
        return "Loam"
    if s >= 85 and c < 10 and si < 15:
        return "Sand"
    if 70 <= s < 90 and c < 15 and si < 30:
        return "Loamy sand"
    if s >= 43 and c < 20 and si < 50:
        return "Sandy loam"
    return "Loam"


def _acidity_class(ph: float) -> str:
    if ph < 5.0:
        return "Strongly acidic"
    if ph < 5.5:
        return "Moderately acidic"
    if ph < 6.5:
        return "Slightly acidic"
    if ph <= 7.3:
        return "Neutral"
    if ph <= 8.0:
        return "Slightly alkaline"
    return "Alkaline"


def analyse(geom, *, include_uncertainty: bool = True) -> Section:
    geom = ensure_shapely(geom)
    sources = [
        Source(
            provider_id=PROVIDER,
            dataset="ISRIC SoilGrids 2.0 — pH, SOC, nitrogen, texture, CEC, bulk density, coarse fragments",
            url="https://docs.isric.org/globaldata/soilgrids/index.html",
            licence="CC BY 4.0",
            resolution="250 m (native equal-area grid)",
            period="Static model v2.0",
            method="ISRIC WebDAV/VRT rasters sampled in their native 250 m equal-area grid; exact polygon mask; area-weighted statistics",
            limitations="Model prediction at 250 m. Not a laboratory test and cannot resolve within-field variation on plots smaller than ~6 ha.",
            access_method="WebDAV/VRT via GDAL range reads (no key)",
            key_required=False,
        )
    ]

    results: dict[str, Any] = {"depths": {}, "profile_0_30": {}, "uncertainty": {}, "texture_class": None}
    warnings: list[str] = []
    errors: list[str] = []
    stale = False
    kinds: set[str] = set()
    retrieval_dates: list[str] = []

    def run(job: tuple[str, str]) -> tuple[str, str, dict[str, Any]]:
        prop, depth = job
        stats, kind = _layer_stats(prop, depth, "mean", geom)
        stats["_kind"] = kind
        return prop, depth, stats

    layer_stats: dict[str, dict[str, Any]] = {}
    jobs = [(prop, depth) for prop in PROPERTIES for depth in DEPTHS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run, job): job for job in jobs}
        for future in as_completed(futures):
            prop, depth = futures[future]
            try:
                _, _, stats = future.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("SoilGrids layer %s %s failed: %s", prop, depth, exc)
                errors.append(f"{prop} {depth}: {type(exc).__name__}: {exc}")
                continue
            layer_stats.setdefault(prop, {})[depth] = stats
            kinds.add(stats["_kind"])
    if not layer_stats:
        raise ProviderError(PROVIDER, "all_layers_failed", "No SoilGrids layer could be sampled")

    for prop, spec in PROPERTIES.items():
        per_depth = layer_stats.get(prop, {})
        if not per_depth:
            continue
        results["depths"][prop] = {
            "label": spec["label"],
            "unit": spec["unit"],
            "values": {
                depth: {
                    "mean": per_depth[depth]["mean"],
                    "median": per_depth[depth]["median"],
                    "p10": per_depth[depth]["p10"],
                    "p90": per_depth[depth]["p90"],
                    "valid_fraction": per_depth[depth]["valid_fraction"],
                    "pixels": per_depth[depth]["pixels_in_polygon"],
                }
                for depth in DEPTHS
                if depth in per_depth
            },
        }

    for prop, spec in PROPERTIES.items():
        per_depth = layer_stats.get(prop, {})
        present = {d: per_depth[d] for d in DEPTHS if d in per_depth and per_depth[d].get("mean") is not None}
        if not present:
            continue
        weight_total = sum(DEPTH_WEIGHT[d] for d in present)
        weighted = sum(present[d]["mean"] * DEPTH_WEIGHT[d] for d in present) / weight_total
        reference = max(present.values(), key=lambda item: item["valid_fraction"] or 0)
        results["profile_0_30"][prop] = {
            "value": round_sig(weighted, spec["decimals"] + 4),
            "unit": spec["unit"],
            "label": spec["label"],
            "valid_fraction": round(
                min(1.0, sum((present[d]["valid_fraction"] or 0) for d in present) / len(present)), 4
            ),
            "sub_field_detail": reference["sub_field_detail"],
            "resolution": reference["resolution"],
            "pixels_in_polygon": reference["pixels_in_polygon"],
            "pixel_area_ha": reference["pixel_area_ha"],
            "single_grid_cell": reference["single_grid_cell"],
        }

    profile = results["profile_0_30"]
    if not profile:
        return Section(
            key="soil",
            status="unavailable",
            data={
                "reason": "no_prediction",
                "modelled": results,
                "source_kind": "model_estimate",
                "message": (
                    "SoilGrids has no 250 m prediction covering this field (a known gap, for example over "
                    "built-up land). No soil value is shown rather than an estimate from elsewhere."
                ),
            },
            sources=sources,
            message="SoilGrids has no prediction for this field's 250 m grid cell.",
            warnings=["A local soil test is the only way to get soil values for this field."],
        )
    sand = (profile.get("sand") or {}).get("value")
    clay = (profile.get("clay") or {}).get("value")
    silt = (profile.get("silt") or {}).get("value")
    results["texture_fractions"] = {"sand": sand, "clay": clay, "silt": silt}
    results["texture_class"] = usda_texture_class(sand, clay, silt)

    ph = (profile.get("phh2o") or {}).get("value")
    if ph is not None:
        results["acidity_class"] = _acidity_class(float(ph))
        results["ph"] = round_sig(ph, 3)

    if include_uncertainty:
        uncertainty: dict[str, Any] = {}
        for depth in UNCERTAINTY_DEPTHS:
            try:
                low_stats, low_kind = _layer_stats("phh2o", depth, "Q0.05", geom)
                high_stats, high_kind = _layer_stats("phh2o", depth, "Q0.95", geom)
                if low_stats["mean"] is not None and high_stats["mean"] is not None:
                    uncertainty[depth] = {
                        "q05": low_stats["mean"],
                        "q95": high_stats["mean"],
                        "unit": "pH",
                    }
            except Exception as exc:  # noqa: BLE001
                log.info("SoilGrids pH uncertainty %s unavailable: %s", depth, exc)
                warnings.append(f"pH uncertainty for {DEPTH_CM[depth][0]}–{DEPTH_CM[depth][1]} cm was unavailable.")
        if uncertainty:
            results["uncertainty"]["phh2o"] = {
                "depths": uncertainty,
                "note": "ISRIC's 90% prediction interval (Q0.05–Q0.95) for topsoil pH.",
            }

    coverage_values = [entry["valid_fraction"] for entry in profile.values() if entry.get("valid_fraction") is not None]
    results["coverage"] = {
        "valid_fraction": round(min(coverage_values), 4) if coverage_values else 0.0,
        "resolution": next(iter(profile.values()))["resolution"] if profile else None,
        "pixels_in_polygon": next(iter(profile.values()))["pixels_in_polygon"] if profile else 0,
        "pixel_area_ha": next(iter(profile.values()))["pixel_area_ha"] if profile else None,
        "sub_field_detail": any(entry["sub_field_detail"] for entry in profile.values()),
        "single_grid_cell": any(entry.get("single_grid_cell") for entry in profile.values()),
        "layer_source": "cached per-field sample" if kinds == {"cache"} else "ISRIC tile read",
    }
    results["limitations"] = [
        "SoilGrids values are model predictions at 250 m, not measurements from your field.",
        "A local soil test is required before lime or fertiliser prescriptions.",
        "Total nitrogen is an estimate of soil nitrogen, not a fertiliser recommendation.",
    ]

    if results["coverage"]["single_grid_cell"]:
        warnings.append(
            "This field is smaller than one 250 m soil grid cell. Soil values describe that cell, not "
            "within-field variation."
        )

    status = STATUS_STALE if stale else STATUS_OK
    message = None
    if errors:
        message = f"{len(errors)} SoilGrids layer(s) could not be sampled; missing values are omitted."

    return Section(
        key="soil",
        status=status,
        data={
            "modelled": results,
            "measured": None,
            "source_kind": "model_estimate",
            "provider_note": "SoilGrids predictions. Laboratory results entered by the farmer override them in guidance.",
            "scale_factors": {prop: spec["scale"] for prop, spec in PROPERTIES.items()},
        },
        sources=sources,
        message=message,
        warnings=warnings,
        retrieved_at=iso_now(),
    )


__all__ = ["analyse", "usda_texture_class", "PROPERTIES", "DEPTHS"]
