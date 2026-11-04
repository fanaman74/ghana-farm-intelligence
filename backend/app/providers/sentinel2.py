"""Copernicus Sentinel-2 L2A vegetation analysis.

Discovery: Element84 earth-search STAC (AWS Open Data sentinel-cogs). Only the
window covering the farm is read from each band COG. Clouds and shadows are
masked with the 20 m Scene Classification Layer, and every scene is ranked by its
*cloud-free fraction over this polygon*, not just its scene-wide cloud cover.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
from shapely.geometry import box

from ..config import GH_TZ, settings
from ..db import cache_get, cache_put
from ..geo.geodesy import ensure_shapely, polygon_hash
from ..geo.raster import (
    align_to_grid,
    colour_array,
    describe_resolution,
    open_raster,
    read_polygon,
    remote_cog,
    save_png,
    sub_field_detail_supported,
    summarise,
    true_colour,
    valid_fraction,
)
from ..utils import iso_now
from .base import ProviderError, Section, Source, STATUS_OK, cached, request
from .registry import provider_map  # noqa: F401  (kept for registry symmetry)

log = logging.getLogger(__name__)

PROVIDER = "sentinel2"
STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"

# SCL classes that are not usable ground observations.
SCL_INVALID = {0, 1, 3, 8, 9, 10, 11}  # no data, saturated, shadow, cloud, cirrus, snow, cloud
ASSET_KEYS = {
    "red": "red",
    "nir": "nir",
    "swir16": "swir16",
    "green": "green",
    "blue": "blue",
    "scl": "scl",
    "visual": "visual",
}


def _bbox(geom) -> list[float]:
    west, south, east, north = geom.bounds
    return [round(west, 5), round(south, 5), round(east, 5), round(north, 5)]


def search_scenes(geom, start: date, end: date, max_cloud: int = 70, limit: int = 60) -> list[dict[str, Any]]:
    geom = ensure_shapely(geom)

    def fetch() -> list[dict[str, Any]]:
        body = {
            "collections": [COLLECTION],
            "bbox": _bbox(geom),
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": max_cloud}},
            "limit": limit,
            "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        }
        payload = request(PROVIDER, "POST", STAC_URL, json_body=body)
        cleaned = []
        for feature in payload.get("features") or []:
            assets = feature.get("assets") or {}
            if not all(key in assets for key in ("red", "nir", "scl")):
                continue
            cleaned.append(
                {
                    "id": feature["id"],
                    "datetime": feature["properties"].get("datetime"),
                    "date": (feature["properties"].get("datetime") or "")[:10],
                    "cloud_cover": feature["properties"].get("eo:cloud_cover"),
                    "platform": feature["properties"].get("platform"),
                    "epsg": feature["properties"].get("proj:epsg"),
                    "bbox": feature.get("bbox"),
                    "assets": {key: assets[key]["href"] for key in ASSET_KEYS if key in assets},
                }
            )
        return cleaned

    payload, _meta = cached(
        ["s2", _bbox(geom), start.isoformat(), end.isoformat(), max_cloud, limit],
        PROVIDER,
        6 * 3600,
        fetch,
    )
    return payload


def _read_band(scene: dict[str, Any], key: str, geom, *, out_shape: tuple[int, int] | None = None):
    href = scene["assets"].get(key)
    if href is None:
        raise ProviderError(PROVIDER, "missing_band", f"Scene {scene['id']} has no '{key}' asset")
    with open_raster(remote_cog(href), GDAL_HTTP_TIMEOUT="60") as ds:
        return read_polygon(ds, geom, out_shape=out_shape)


def _aligned(band_window, reference) -> np.ndarray:
    if band_window.shape == reference.shape and np.allclose(band_window.transform[:6], reference.transform[:6]):
        return band_window.data[0].astype("float64")
    return align_to_grid(
        band_window.data[0],
        band_window.transform,
        band_window.crs,
        reference.transform,
        reference.crs,
        reference.shape,
    ).astype("float64")


def _cloud_free(scene: dict[str, Any], geom) -> dict[str, Any]:
    geom_hash = polygon_hash(geom)
    cache_key = f"{PROVIDER}:scl:{scene['id']}:{geom_hash}"
    entry = cache_get(cache_key)
    if entry and not entry["expired"]:
        return entry["payload"]

    rw = _read_band(scene, "scl", geom)
    scl = rw.data[0].astype("int32")
    valid = rw.mask & ~np.isin(scl, list(SCL_INVALID))
    result = {
        "cloud_free_fraction": round(valid_fraction(rw, valid), 4),
        "pixels": int(rw.mask.sum()),
        "valid_pixels": int(valid.sum()),
    }
    cache_put(cache_key, PROVIDER, result, 30 * 24 * 3600)
    return result


def _spatial_pattern(ndvi: np.ndarray, valid: np.ndarray) -> dict[str, Any] | None:
    """Relative within-field vigour pattern. The cause is never inferred."""
    if not valid.any():
        return None
    values = ndvi[valid]
    mean = float(np.nanmean(values))
    sd = float(np.nanstd(values))
    if not np.isfinite(sd) or sd == 0:
        return None
    threshold = mean - sd
    low = valid & (ndvi < threshold)

    rows, cols = ndvi.shape
    quadrants = {
        "north-west": (slice(0, rows // 2), slice(0, cols // 2)),
        "north-east": (slice(0, rows // 2), slice(cols // 2, cols)),
        "south-west": (slice(rows // 2, rows), slice(0, cols // 2)),
        "south-east": (slice(rows // 2, rows), slice(cols // 2, cols)),
    }
    summary = []
    for label, (row_slice, col_slice) in quadrants.items():
        sub_valid = valid[row_slice, col_slice]
        if sub_valid.sum() < 10:
            continue
        summary.append(
            {
                "quadrant": label,
                "mean_ndvi": round(float(np.nanmean(ndvi[row_slice, col_slice][sub_valid])), 3),
                "pixels": int(sub_valid.sum()),
            }
        )
    summary.sort(key=lambda item: item["mean_ndvi"])
    return {
        "field_mean_ndvi": round(mean, 3),
        "field_sd_ndvi": round(sd, 3),
        "low_vigour_threshold": round(threshold, 3),
        "low_vigour_share": round(float(low[valid].sum() / valid.sum()), 3),
        "quadrants_lowest_first": summary,
        "note": "Relative pattern inside this field from one cloud-masked image. Not a pest, disease or nutrient diagnosis.",
    }


def _scene_result(scene: dict[str, Any], geom, *, full: bool) -> dict[str, Any]:
    red_window = _read_band(scene, "red", geom)
    reference = red_window
    red = reference.data[0].astype("float64")
    nir = _aligned(_read_band(scene, "nir", geom), reference)
    scl = _aligned(_read_band(scene, "scl", geom), reference).astype("int32")

    cloud_free = ~np.isin(scl, list(SCL_INVALID))
    band_valid = (red > 0) & np.isfinite(red) & (nir > 0) & np.isfinite(nir)
    valid = reference.mask & band_valid & cloud_free
    weights = reference.weights

    ndvi = np.where(band_valid, (nir - red) / (nir + red + 1e-9), np.nan)
    stats = summarise(ndvi, valid, weights, decimals=4)
    stats["valid_fraction"] = round(valid_fraction(reference, valid), 4)

    coverage = {
        "valid_fraction": round(valid_fraction(reference, valid), 4),
        "cloud_free_fraction": round(valid_fraction(reference, cloud_free), 4),
        "pixels": int(reference.mask.sum()),
        "resolution": describe_resolution(reference),
        "corners": reference.corners_wgs84(),
        "sub_field_detail": sub_field_detail_supported(reference.polygon_area_m2, reference.pixel_area_m2),
        "polygon_area_ha": round(reference.polygon_area_m2 / 10_000, 3),
    }

    result: dict[str, Any] = {
        "id": scene["id"],
        "date": scene["date"],
        "datetime": scene["datetime"],
        "platform": scene.get("platform"),
        "scene_cloud_cover": scene.get("cloud_cover"),
        "cloud_free_fraction": coverage["cloud_free_fraction"],
        "valid_fraction": coverage["valid_fraction"],
        "ndvi": stats,
        "pixels": coverage["pixels"],
        "resolution": coverage["resolution"],
        "sub_field_detail": coverage["sub_field_detail"],
        "image_corners": coverage["corners"],
        "polygon_area_ha": coverage["polygon_area_ha"],
    }

    if full:
        swir = _aligned(_read_band(scene, "swir16", geom), reference)
        ndmi = np.where((nir > 0) & (swir > 0), (nir - swir) / (nir + swir + 1e-9), np.nan)
        ndmi_valid = valid & (swir > 0)
        result["ndmi"] = summarise(ndmi, ndmi_valid, weights, decimals=4)

        green = _aligned(_read_band(scene, "green", geom), reference)
        blue = _aligned(_read_band(scene, "blue", geom), reference)
        rgb = np.stack([red, green, blue])
        rgba = true_colour(rgb, valid & (green > 0) & (blue > 0))

        chip_dir = settings.cache_dir / "chips"
        chip_dir.mkdir(parents=True, exist_ok=True)
        base = f"{scene['id']}_{polygon_hash(geom)}"
        paths = {
            "true_color": chip_dir / f"{base}_truecolour.png",
            "ndvi": chip_dir / f"{base}_ndvi.png",
            "ndmi": chip_dir / f"{base}_ndmi.png",
        }
        save_png(rgba, paths["true_color"])
        save_png(colour_array(ndvi, valid, "ndvi", -0.2, 0.95), paths["ndvi"])
        save_png(colour_array(ndmi, ndmi_valid, "ndmi", -0.4, 0.5), paths["ndmi"])
        result["chips"] = {key: f"/api/chips/{path.name}" for key, path in paths.items()}
        result["spatial_pattern"] = _spatial_pattern(ndvi, valid)

    return result


def analyse(geom, *, months: int = 12, max_candidates: int = 8) -> Section:
    geom = ensure_shapely(geom)
    end = datetime.now(GH_TZ).date()
    start = end - timedelta(days=months * 30)
    scenes = search_scenes(geom, start, end, max_cloud=70, limit=80)

    if not scenes:
        return Section(
            key="vegetation",
            status="unavailable",
            data={"reason": "no_scenes", "searched": {"start": start.isoformat(), "end": end.isoformat()}},
            sources=[_source()],
            message=(
                "No Sentinel-2 scenes matched this area and period. This is a data availability result, "
                "not evidence about the crop."
            ),
        )

    recent = scenes[: max_candidates * 2]
    evaluated: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_cloud_free, scene, geom): scene for scene in recent}
        for future in as_completed(futures):
            scene = futures[future]
            try:
                info = future.result()
            except Exception as exc:  # noqa: BLE001
                log.info("SCL read failed for %s: %s", scene["id"], exc)
                continue
            evaluated.append({**scene, **info})

    if not evaluated:
        raise ProviderError(PROVIDER, "no_readable_scene", "Sentinel-2 scenes were found but none could be read")
    evaluated.sort(key=lambda item: item["datetime"] or "", reverse=True)
    usable = [item for item in evaluated if item["cloud_free_fraction"] >= 0.30]
    newest = usable[0] if usable else evaluated[0]
    # Newest is not automatically most useful: a scene that is 34 % cloud-free
    # over the field paints two-thirds of it transparent. Among recent candidates,
    # take a clearly clearer scene, and say so when that is what happened.
    newest_date = date.fromisoformat(newest["date"])
    recent_clear = [
        item
        for item in usable
        if date.fromisoformat(item["date"]) >= newest_date - timedelta(days=60)
    ]
    clearest = max(
        recent_clear,
        key=lambda item: (item["cloud_free_fraction"], item["datetime"] or ""),
        default=None,
    )
    if clearest is not None and clearest["cloud_free_fraction"] >= newest["cloud_free_fraction"] + 0.15:
        latest = clearest
    else:
        latest = newest

    trend_candidates: list[dict[str, Any]] = [latest]
    for item in evaluated:
        if item["id"] == latest["id"] or item["cloud_free_fraction"] < 0.15:
            continue
        if all(
            abs((date.fromisoformat(item["date"]) - date.fromisoformat(existing["date"])).days) >= 45
            for existing in trend_candidates
        ):
            trend_candidates.append(item)
        if len(trend_candidates) >= 5:
            break

    warnings: list[str] = []
    if latest["id"] != evaluated[0]["id"]:
        newest_scene = evaluated[0]
        warnings.append(
            f"The newest image ({newest_scene['date']}, "
            f"{newest_scene['cloud_free_fraction'] * 100:.0f}% cloud-free over this field) was cloudier than the "
            f"{latest['date']} scene ({latest['cloud_free_fraction'] * 100:.0f}%), which the values and overlays use."
        )
    results: list[dict[str, Any]] = []
    for item in trend_candidates:
        try:
            results.append(_scene_result(item, geom, full=item["id"] == latest["id"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("Sentinel-2 scene %s failed: %s", item["id"], exc)
            warnings.append(f"Scene {item['date']} could not be processed.")

    if not results:
        raise ProviderError(PROVIDER, "all_scenes_failed", "No Sentinel-2 scene could be processed for this polygon")

    results.sort(key=lambda item: item["date"])
    latest_result = next((item for item in results if item["id"] == latest["id"]), results[-1])
    history = [
        {
            "date": item["date"],
            "scene_id": item["id"],
            "ndvi_mean": item["ndvi"]["mean"],
            "ndvi_median": item["ndvi"]["median"],
            "cloud_free_fraction": item["cloud_free_fraction"],
            "valid_fraction": item["valid_fraction"],
        }
        for item in results
    ]
    means = [item["ndvi_mean"] for item in history if item["ndvi_mean"] is not None]
    trend = round(float(np.polyfit(range(len(means)), means, 1)[0]), 4) if len(means) >= 3 else None
    previous_means = [
        item["ndvi_mean"]
        for item in history
        if item["scene_id"] != latest_result["id"] and item["ndvi_mean"] is not None
    ]
    change = None
    if previous_means and latest_result["ndvi"].get("mean") is not None:
        change = round(float(latest_result["ndvi"]["mean"]) - float(np.median(previous_means)), 3)

    if latest_result.get("cloud_free_fraction", 0) < 0.3:
        warnings.append(
            "The most recent usable image is partly cloudy over this field "
            f"({latest_result.get('cloud_free_fraction', 0) * 100:.0f}% cloud-free). Interpret values cautiously."
        )
    if latest_result["ndvi"].get("status") == "no_valid_pixels":
        warnings.append("No cloud-free pixels were available inside this field for the latest scene.")

    return Section(
        key="vegetation",
        status=STATUS_OK,
        data={
            "latest": latest_result,
            "history": history,
            "trend_per_scene": trend,
            "change_vs_recent_median": change,
            "scenes_evaluated": len(evaluated),
            "scenes_available": len(scenes),
            "search_window": {"start": start.isoformat(), "end": end.isoformat()},
            "source_kind": "satellite_observation",
            "notes": [
                "NDVI/NDMI use only cloud- and shadow-free pixels inside the polygon.",
                "Vegetation indices show relative condition; they do not identify pests, diseases or nutrient deficiencies.",
                "A nominal revisit interval is not a guarantee of a usable cloud-free image.",
            ],
        },
        sources=[_source(latest_available=latest_result["date"], scenes=len(history))],
        warnings=warnings,
        retrieved_at=iso_now(),
    )


def _source(latest_available: str | None = None, scenes: int | None = None) -> Source:
    return Source(
        provider_id=PROVIDER,
        dataset="Copernicus Sentinel-2 L2A (10 m/20 m) — AWS Open Data COGs via Element84 STAC",
        url="https://registry.opendata.aws/sentinel-2-l2a-cogs/",
        licence="Copernicus Sentinel data — free and open (contains modified Copernicus Sentinel data)",
        resolution="10 m (red/NIR), 20 m (SWIR, SCL resampled to 10 m)",
        period=f"latest usable {latest_available}; {scenes or 0} scenes in trend" if latest_available else None,
        method="STAC search; ranking by polygon cloud-free fraction; polygon-masked NDVI/NDMI with area weights",
        limitations="Cloud cover limits usable dates. Vegetation stress is not a diagnosis; field scouting is required.",
        access_method="STAC + COG range reads (no key)",
        key_required=False,
    )

# --------------------------------------------------------------------------- #
# Satellite basemap context (a wider true-colour image around the farm)
# --------------------------------------------------------------------------- #
def _context_geometry(geom, buffer_m: float):
    """Square-ish context box around the polygon, in degrees, respecting latitude."""
    centroid = geom.centroid
    lat = float(centroid.y)
    dlat = buffer_m / 110_574.0
    dlon = buffer_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    west, south, east, north = geom.bounds
    return box(west - dlon, south - dlat, east + dlon, north + dlat)


def _scene_cloud_fraction(scene: dict[str, Any], geom) -> float:
    rw = _read_band(scene, "scl", geom, out_shape=(160, 160))
    scl = rw.data[0].astype("int32")
    if scl.size == 0:
        return 1.0
    return float(np.isin(scl, [3, 8, 9, 10, 11]).mean())


def _covers(scene: dict[str, Any], geom) -> bool:
    bbox = scene.get("bbox")
    if not bbox:
        return False
    west, south, east, north = geom.bounds
    return bbox[0] <= west and bbox[1] <= south and bbox[2] >= east and bbox[3] >= north


def context_image(geom, *, buffer_m: int = 1500, max_size: int = 640) -> dict[str, Any]:
    """True-colour Sentinel-2 image for the farm plus its surroundings.

    This is a real 10 m satellite scene, downsampled for a phone-sized basemap, not
    high-resolution aerial imagery. The least-cloudy recent scene over the area is
    used; clouds are left visible rather than painted out.
    """
    geom = ensure_shapely(geom)
    context = _context_geometry(geom, buffer_m)
    cache_key = ["s2context", _bbox(context), buffer_m, max_size]

    def fetch() -> dict[str, Any]:
        today = datetime.now(GH_TZ).date()
        try:
            scenes = search_scenes(context, today - timedelta(days=120), today, max_cloud=70, limit=60)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": f"Sentinel-2 search failed: {exc}"}
        candidates = [scene for scene in scenes if scene["assets"].get("visual")]
        covering = [scene for scene in candidates if _covers(scene, context)]
        pool = (covering or candidates)[:4]
        if not pool:
            return {
                "available": False,
                "reason": "No recent Sentinel-2 scene with a true-colour asset covers this area.",
            }

        best_cloud = None
        best_scene = None
        for scene in pool[:3]:
            try:
                fraction = _scene_cloud_fraction(scene, context)
            except Exception:  # noqa: BLE001
                fraction = 1.0
            if best_scene is None or fraction < best_cloud:
                best_cloud = fraction
                best_scene = scene
        assert best_scene is not None and best_cloud is not None

        west, south, east, north = context.bounds
        width_m = (east - west) * 111_320.0 * math.cos(math.radians((south + north) / 2))
        height_m = (north - south) * 110_574.0
        if width_m >= height_m:
            out_w = max_size
            out_h = max(1, round(max_size * height_m / max(width_m, 1)))
        else:
            out_h = max_size
            out_w = max(1, round(max_size * width_m / max(height_m, 1)))

        with open_raster(remote_cog(best_scene["assets"]["visual"]), GDAL_HTTP_TIMEOUT="90") as ds:
            rw = read_polygon(ds, context, bands=(1, 2, 3), out_shape=(out_h, out_w))

        image = rw.data.astype("float32").transpose(1, 2, 0)
        valid = rw.mask & (image.max(axis=2) > 0)
        rgba = np.zeros((*valid.shape, 4), dtype="uint8")
        if valid.any():
            for channel in range(3):
                values = image[..., channel][valid]
                low, high = np.percentile(values, [2, 98]) if values.size else (0.0, 255.0)
                if high <= low:
                    high = low + 1.0
                scaled = np.clip((image[..., channel] - low) / (high - low), 0, 1)
                rgba[..., channel] = (scaled * 255).astype("uint8")
            rgba[..., 3] = 255
        filename = f"satellite_{polygon_hash(context)}.jpg"
        from PIL import Image

        (settings.cache_dir / "chips").mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgba[..., :3], mode="RGB").save(
            settings.cache_dir / "chips" / filename, format="JPEG", quality=82, optimize=True
        )
        return {
            "available": True,
            "url": f"/api/chips/{filename}",
            "corners": rw.corners_wgs84(),
            "scene_id": best_scene["id"],
            "scene_date": best_scene["date"],
            "scene_cloud_cover": best_scene.get("cloud_cover"),
            "cloud_fraction_over_area": round(best_cloud, 3),
            "platform": best_scene.get("platform"),
            "resolution_m": 10,
            "buffer_m": buffer_m,
            "pixels": [out_w, out_h],
            "source": {
                "provider_id": PROVIDER,
                "dataset": "Copernicus Sentinel-2 L2A — true colour",
                "licence": "Copernicus Sentinel data — free and open (contains modified Copernicus Sentinel data)",
                "url": "https://registry.opendata.aws/sentinel-2-l2a-cogs/",
                "resolution_m": 10,
            },
            "note": (
                "Sentinel-2 10 m true colour, not high-resolution aerial imagery. "
                "Clouds are left visible; the scene date is the acquisition date."
            ),
            "generated_at": iso_now(),
        }

    payload, _meta = cached(cache_key, PROVIDER, 7 * 24 * 3600, fetch)
    return payload
