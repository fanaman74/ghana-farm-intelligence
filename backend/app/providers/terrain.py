"""Terrain from Copernicus DEM GLO-30 (AWS Open Data COGs).

Reads at most the 1° tiles that intersect the polygon, mosaics them on their
native 30 m grid, then derives elevation, slope and aspect inside the polygon.
This is a screening aid, not an engineering-grade terrain or flood model.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from pyproj import Geod

from ..geo.geodesy import ensure_shapely
from ..geo.raster import open_raster, remote_cog, describe_resolution_deg
from ..utils import iso_now
from .base import ProviderError, Section, Source, STATUS_OK
from .registry import provider_map  # noqa: F401

GEOD = Geod(ellps="WGS84")
DEM_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
RES = 1 / 3600  # 1 arc-second


def dem_url(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    name = f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_DEM"
    return f"{DEM_BASE}/{name}/{name}.tif"


def _tiles(geom) -> list[tuple[int, int]]:
    west, south, east, north = geom.bounds
    tiles = []
    for lat in range(math.floor(south), math.floor(north) + 1):
        for lon in range(math.floor(west), math.floor(east) + 1):
            tiles.append((lat, lon))
    return tiles


def mosaic(geom) -> tuple[np.ndarray, Any, float, float, dict[str, int]]:
    """Return (elevation array, affine transform, centre lat, centre lon, info)."""
    west, south, east, north = geom.bounds
    # Snap the output grid to the global 1 arc-second pixel grid.
    out_west = math.floor(west / RES) * RES
    out_north = math.ceil(north / RES) * RES
    out_east = math.ceil(east / RES) * RES
    out_south = math.floor(south / RES) * RES
    cols = int(round((out_east - out_west) / RES))
    rows = int(round((out_north - out_south) / RES))
    if cols <= 0 or rows <= 0 or cols * rows > 40_000_000:
        raise ProviderError("copernicus_dem", "too_large", "The terrain window for this boundary is too large")
    mosaic_array = np.full((rows, cols), np.nan, dtype="float32")
    from rasterio.transform import from_origin

    transform = from_origin(out_west, out_north, RES, RES)
    tiles = _tiles(geom)
    errors = 0
    for lat_floor, lon_floor in tiles:
        try:
            with open_raster(remote_cog(dem_url(lat_floor, lon_floor)), GDAL_HTTP_TIMEOUT="60") as ds:
                from rasterio.windows import from_bounds

                window = from_bounds(
                    max(out_west, lon_floor), max(out_south, lat_floor),
                    min(out_east, lon_floor + 1), min(out_north, lat_floor + 1),
                    transform=ds.transform,
                )
                data = ds.read(1, window=window)
                nodata = ds.nodata
                if nodata is not None:
                    # The DEM's void/sea sentinel is not an elevation. Without this
                    # it becomes a real value and corrupts the slope gradient.
                    data = np.where(data == nodata, np.nan, data)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            _ = exc
            continue
        # Position of this tile window inside the output mosaic.
        col_off = int(round((max(out_west, lon_floor) - out_west) / RES))
        row_off = int(round((out_north - min(out_north, lat_floor + 1)) / RES))
        tile_rows, tile_cols = data.shape
        row_off = max(0, row_off)
        col_off = max(0, col_off)
        row_end = min(rows, row_off + tile_rows)
        col_end = min(cols, col_off + tile_cols)
        if row_end <= row_off or col_end <= col_off:
            continue
        mosaic_array[row_off:row_end, col_off:col_end] = data[
            : row_end - row_off, : col_end - col_off
        ]
    if np.isnan(mosaic_array).all():
        raise ProviderError("copernicus_dem", "no_tiles", "No Copernicus DEM tile could be read for this area")
    centre_lat = (south + north) / 2
    centre_lon = (west + east) / 2
    return mosaic_array, transform, centre_lat, centre_lon, {"requested": len(tiles), "failed": errors}


def _ground_pixel_size(lat: float, lon: float) -> tuple[float, float]:
    _, _, dx = GEOD.inv(lon, lat, lon + RES, lat)
    _, _, dy = GEOD.inv(lon, lat, lon, lat + RES)
    return abs(dx), abs(dy)


def _polygon_mask(geom, transform, shape) -> np.ndarray:
    from rasterio.features import rasterize
    from shapely.geometry import mapping

    mask = rasterize(
        [(mapping(geom), 1)], out_shape=shape, transform=transform, fill=0, all_touched=True, dtype="uint8"
    )
    return mask.astype(bool)


def _stats(values: np.ndarray, valid: np.ndarray, weights: np.ndarray) -> dict[str, Any]:
    selected = values[valid]
    if selected.size == 0:
        return {"mean": None, "min": None, "max": None, "p10": None, "p90": None, "pixels": 0}
    w = weights[valid]
    order = np.argsort(selected)
    cumulative = np.cumsum(w[order]) - 0.5 * w[order]
    cumulative /= cumulative[-1]
    return {
        "mean": round(float(np.average(selected, weights=w)), 1),
        "min": round(float(selected.min()), 1),
        "max": round(float(selected.max()), 1),
        "p10": round(float(np.interp(0.10, cumulative, selected[order])), 1),
        "p90": round(float(np.interp(0.90, cumulative, selected[order])), 1),
        "pixels": int(selected.size),
    }


def analyse(geom, *, annual_rainfall_mm: float | None = None) -> Section:
    geom = ensure_shapely(geom)
    elevation, transform, centre_lat, centre_lon, dem_info = mosaic(geom)
    mask = _polygon_mask(geom, transform, elevation.shape)
    finite = np.isfinite(elevation)
    valid = mask & finite
    if not valid.any():
        raise ProviderError("copernicus_dem", "all_nodata", "The DEM window contains no valid elevation inside this field")
    pixels_in_polygon = int(mask.sum()) or 1
    coverage = float(valid.sum() / pixels_in_polygon)
    warnings: list[str] = []
    if dem_info.get("failed"):
        warnings.append(
            f"{dem_info['failed']} of {dem_info['requested']} Copernicus DEM tile(s) could not be read; "
            "elevation and slope describe only the part of the boundary that loaded."
        )
    if coverage < 0.95:
        warnings.append(
            f"Only {coverage * 100:.0f}% of this boundary has valid elevation data; slope statistics exclude the rest."
        )

    dx, dy = _ground_pixel_size(centre_lat, centre_lon)
    # Slope in percent from the 3x3 gradient, edge-safe.
    filled = np.where(finite, elevation, np.nan)
    grad_y, grad_x = np.gradient(filled)
    slope_pct = np.sqrt((grad_x / dx) ** 2 + (grad_y / dy) ** 2) * 100.0
    aspect_deg = (np.degrees(np.arctan2(-grad_x / dx, grad_y / dy)) + 360) % 360
    slope_valid = valid & np.isfinite(slope_pct)

    pixel_area = dx * dy
    weights = np.where(valid, pixel_area, 0.0)

    elev_stats = _stats(elevation, valid, weights)
    slope_stats = _stats(np.clip(slope_pct, 0, 200), slope_valid, np.where(slope_valid, pixel_area, 0.0))

    aspect_values = aspect_deg[slope_valid]
    aspect_mean = None
    if aspect_values.size:
        radians = np.radians(aspect_values)
        aspect_mean = round(float(np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())) % 360), 0)

    flat_share = float(((slope_pct < 2) & slope_valid).sum() / max(slope_valid.sum(), 1))
    steep_share = float(((slope_pct > 15) & slope_valid).sum() / max(slope_valid.sum(), 1))

    erosion_risk = "low"
    if slope_stats["mean"] is not None:
        if slope_stats["mean"] > 15 or steep_share > 0.35:
            erosion_risk = "high"
        elif slope_stats["mean"] > 8 or steep_share > 0.15:
            erosion_risk = "moderate"
    if annual_rainfall_mm and annual_rainfall_mm > 1400 and erosion_risk == "moderate":
        erosion_risk = "high"

    return Section(
        key="terrain",
        status=STATUS_OK,
        data={
            "elevation_m": elev_stats,
            "slope_percent": slope_stats,
            "slope_degrees": {
                "mean": round(math.degrees(math.atan(slope_stats["mean"] / 100)), 2)
                if slope_stats["mean"] is not None
                else None
            },
            "aspect_degrees": aspect_mean,
            "aspect_label": _aspect_label(aspect_mean),
            "flat_share": round(flat_share, 3),
            "steep_share_over_15pct": round(steep_share, 3),
            "erosion_screening": {
                "level": erosion_risk,
                "basis": "Mean slope, share above 15 %, and annual rainfall context",
                "note": "A screening flag only. It does not model runoff, gullies or flood depth.",
            },
            "pixel_size_m": [round(dx, 1), round(dy, 1)],
            "resolution": describe_resolution_deg(RES, RES, centre_lat),
            "dem_coverage_fraction": round(coverage, 4),
            "tiles": dem_info,
            "source_kind": "derived_from_dem",
            "notes": [
                "30 m elevation cannot resolve field drainage or waterlogging.",
                "Slope is a screening aid, not a cadastral survey.",
            ],
        },
        sources=[
            Source(
                provider_id="copernicus_dem",
                dataset="Copernicus DEM GLO-30 (30 m)",
                url="https://registry.opendata.aws/copernicus-dem/",
                licence="Copernicus DEM — © DLR e.V. 2010-2014 and © Airbus DS 2014-2018, provided under COPERNICUS by the EU and ESA",
                resolution="30 m (1 arc-second)",
                period="Static (2011–2015 acquisition, 2021 release)",
                method="1° tile windows mosaicked; polygon mask; slope from 3×3 gradient with geodesic pixel spacing",
                limitations="A 30 m DEM cannot resolve field-scale drainage; not a flood model.",
                access_method="AWS Open Data COGs (no key)",
                key_required=False,
            )
        ],
        warnings=warnings,
        retrieved_at=iso_now(),
    )


def _aspect_label(degrees: float | None) -> str | None:
    if degrees is None:
        return None
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((degrees + 22.5) % 360 // 45)]
