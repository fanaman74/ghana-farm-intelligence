"""Render farm-scale PNG overlays for soil, land-cover and terrain layers.

Every layer is produced from the same polygon-masked rasters used for the
statistics, so what the farmer sees on the map is the data behind the numbers.
SoilGrids and DEM layers are drawn at their native resolution and the legend says
so; a 25 ha field really is only a handful of 250 m soil cells.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from affine import Affine
import numpy as np
from PIL import Image
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import mapping
from shapely.ops import transform as shapely_transform

from ..config import settings
from ..db import cache_get, cache_put
from ..geo.geodesy import ensure_shapely
from ..geo.raster import open_raster, read_polygon, remote_cog
from ..utils import iso_now, stable_hash
from ..providers.landcover import CLASSES as LANDCOVER_CLASSES, tile_url as landcover_tile
from ..providers.soilgrids import PROPERTIES
from ..providers.soilgrids_layers import open_layer
from ..providers.terrain import _ground_pixel_size, mosaic

log = logging.getLogger(__name__)

LAYER_TTL = 180 * 24 * 3600
CHIP_DIR = settings.cache_dir / "chips"


# --------------------------------------------------------------------------- #
# Legends and palettes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LayerSpec:
    layer_id: str
    title: str
    unit: str
    resolution_m: float
    breaks: tuple[float, ...]
    labels: tuple[str, ...]
    colours: tuple[str, ...]
    note: str
    provider_id: str
    dataset: str
    licence: str
    source_url: str
    depth: str | None = None


SOIL_PH = LayerSpec(
    layer_id="soil_ph",
    title="Soil pH (0–5 cm)",
    unit="pH",
    resolution_m=250,
    breaks=(5.0, 5.5, 6.0, 6.5, 7.3),
    labels=("≤5.0 very acid", "5.0–5.5", "5.5–6.0", "6.0–6.5", "6.5–7.3", ">7.3 alkaline"),
    colours=("#8c510a", "#bf812d", "#dfc27d", "#c7eae5", "#5ab4ac", "#01665e"),
    note="SoilGrids 250 m model prediction, clipped to your boundary. Each block is one 250 m cell; a 25 ha field covers about 4.",
    provider_id="soilgrids",
    dataset="ISRIC SoilGrids 2.0 — pH (0–5 cm mean)",
    licence="CC BY 4.0",
    source_url="https://docs.isric.org/globaldata/soilgrids/index.html",
    depth="0-5cm",
)

SOIL_CARBON = LayerSpec(
    layer_id="soil_carbon",
    title="Soil organic carbon (0–5 cm)",
    unit="g/kg",
    resolution_m=250,
    breaks=(5.0, 10.0, 20.0, 40.0),
    labels=("≤5", "5–10", "10–20", "20–40", ">40"),
    colours=("#ffffd9", "#c7e9b4", "#7fcdbb", "#41b6c4", "#225ea8"),
    note="SoilGrids prediction at 250 m. A laboratory test is the only measurement.",
    provider_id="soilgrids",
    dataset="ISRIC SoilGrids 2.0 — soil organic carbon (0–5 cm mean)",
    licence="CC BY 4.0",
    source_url="https://docs.isric.org/globaldata/soilgrids/index.html",
    depth="0-5cm",
)

SOIL_CLAY = LayerSpec(
    layer_id="soil_texture",
    title="Clay content (0–5 cm)",
    unit="%",
    resolution_m=250,
    breaks=(10.0, 20.0, 35.0, 50.0),
    labels=("≤10 %", "10–20 %", "20–35 %", "35–50 %", ">50 %"),
    colours=("#f6e8c3", "#dfc27d", "#bf812d", "#8c510a", "#543005"),
    note="Clay fraction from SoilGrids at 250 m; the texture class is reported for the field's grid cell.",
    provider_id="soilgrids",
    dataset="ISRIC SoilGrids 2.0 — clay (0–5 cm mean)",
    licence="CC BY 4.0",
    source_url="https://docs.isric.org/globaldata/soilgrids/index.html",
    depth="0-5cm",
)

SLOPE = LayerSpec(
    layer_id="slope",
    title="Slope",
    unit="%",
    resolution_m=30,
    breaks=(2.0, 5.0, 10.0, 20.0),
    labels=("≤2 %", "2–5 %", "5–10 %", "10–20 %", ">20 %"),
    colours=("#440154", "#414487", "#2a788e", "#22a884", "#fde725"),
    note="Derived from Copernicus DEM GLO-30. A screening aid, not an engineering survey.",
    provider_id="copernicus_dem",
    dataset="Copernicus DEM GLO-30 — derived slope",
    licence="Copernicus DEM — © DLR e.V. 2010-2014 and © Airbus DS 2014-2018",
    source_url="https://registry.opendata.aws/copernicus-dem/",
)

ELEVATION = LayerSpec(
    layer_id="elevation",
    title="Elevation",
    unit="m",
    resolution_m=30,
    breaks=(150.0, 250.0, 350.0, 500.0),
    labels=("≤150 m", "150–250 m", "250–350 m", "350–500 m", ">500 m"),
    colours=("#f7fcfd", "#ccece6", "#66c2a4", "#2ca25f", "#006d2c"),
    note="Copernicus DEM GLO-30 (30 m). Enable 3D terrain for relief.",
    provider_id="copernicus_dem",
    dataset="Copernicus DEM GLO-30 — elevation",
    licence="Copernicus DEM — © DLR e.V. 2010-2014 and © Airbus DS 2014-2018",
    source_url="https://registry.opendata.aws/copernicus-dem/",
)

SPECS: dict[str, LayerSpec] = {
    spec.layer_id: spec for spec in (SOIL_PH, SOIL_CARBON, SOIL_CLAY, SLOPE, ELEVATION)
}

CROPLAND_ORDER = (40, 10, 30, 20, 50, 60, 90, 80, 95, 100)
CROPLAND_NOTE = (
    "ESA WorldCover 2021 is a historical baseline, not today's crop. "
    "Cropland includes fallow and mixed systems."
)


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _rgba_from_classes(
    values: np.ndarray, valid: np.ndarray, breaks: tuple[float, ...], colours: tuple[str, ...]
) -> np.ndarray:
    indices = np.digitize(values, breaks)
    rgba = np.zeros((*values.shape, 4), dtype="uint8")
    for bin_index, colour in enumerate(colours):
        selected = valid & (indices == bin_index)
        if not selected.any():
            continue
        rgba[selected, 0:3] = _hex(colour)
        rgba[selected, 3] = 230
    return rgba


def _corners(transform: Any, width: int, height: int, crs: Any) -> list[list[float]]:
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    output = []
    for col, row in ((0, 0), (width, 0), (width, height), (0, height)):
        x, y = transform @ (col, row)
        lon, lat = transformer.transform(x, y)
        output.append([round(float(lon), 7), round(float(lat), 7)])
    return output


def _polygon_mask(geom, transform: Affine, shape: tuple[int, int], crs: Any, all_touched: bool) -> np.ndarray:
    boundary_geom = geom
    if crs is not None and "4326" not in str(crs):
        to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        boundary_geom = shapely_transform(to_crs.transform, geom)
    return rasterize(
        [(mapping(boundary_geom), 1)],
        out_shape=shape,
        transform=transform,
        fill=0,
        all_touched=all_touched,
        dtype="uint8",
    ).astype(bool)


def _refine(
    values: np.ndarray,
    valid: np.ndarray,
    transform: Affine,
    geom,
    factor: int,
    crs: Any,
) -> tuple[np.ndarray, np.ndarray, Affine]:
    """Upsample a coarse grid for display, then clip it to the boundary.

    The values keep their native resolution (each block is one source cell), but the
    overlay edge follows the field instead of the 250 m/30 m cell edges. This is
    cartography, not a claim of finer measurement.
    """
    if factor <= 1:
        return values, valid, transform
    values = np.repeat(np.repeat(values, factor, axis=0), factor, axis=1)
    valid = np.repeat(np.repeat(valid, factor, axis=0), factor, axis=1)
    fine = Affine(
        transform.a / factor,
        transform.b / factor,
        transform.c,
        transform.d / factor,
        transform.e / factor,
        transform.f,
    )
    boundary_geom = geom
    if crs is not None and "4326" not in str(crs):
        to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        boundary_geom = shapely_transform(to_crs.transform, geom)
    boundary = rasterize(
        [(mapping(boundary_geom), 1)],
        out_shape=values.shape,
        transform=fine,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)
    return values, valid & boundary, fine

def _save_png(rgba: np.ndarray, filename: str) -> None:
    CHIP_DIR.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(CHIP_DIR / filename, format="PNG", optimize=True)


def _source(spec: LayerSpec) -> dict[str, Any]:
    return {
        "provider_id": spec.provider_id,
        "dataset": spec.dataset,
        "licence": spec.licence,
        "url": spec.source_url,
        "resolution_m": spec.resolution_m,
    }


def _result(spec: LayerSpec, filename: str, corners: list[list[float]], legend: list[dict[str, str]],
            values_valid: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    return {
        "available": True,
        "layer_id": spec.layer_id,
        "title": spec.title,
        "url": f"/api/chips/{filename}",
        "corners": corners,
        "legend": legend,
        "resolution_m": spec.resolution_m,
        "unit": spec.unit,
        "source": _source(spec),
        "note": spec.note,
        "pixels": int(values_valid.sum()),
        "value_range": (
            [round(float(np.nanmin(values[values_valid])), 2), round(float(np.nanmax(values[values_valid])), 2)]
            if values_valid.any()
            else None
        ),
        "generated_at": iso_now(),
    }


def _legend_for(spec: LayerSpec) -> list[dict[str, str]]:
    return [
        {"color": colour, "label": label}
        for colour, label in zip(spec.colours, spec.labels)
    ]


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def _render_soil(spec: LayerSpec, geom) -> dict[str, Any]:
    property_id = {"soil_ph": "phh2o", "soil_carbon": "soc", "soil_texture": "clay"}[spec.layer_id]
    meta = PROPERTIES[property_id]
    with open_layer(property_id, spec.depth or "0-5cm", "mean") as dataset:
        rw = read_polygon(dataset, geom)
    raw = rw.data[0]
    nodata = rw.nodata if rw.nodata is not None else -32768.0
    scaled = raw / meta["scale"]
    low, high = meta["range"]
    # Any 250 m cell that intersects the field is shown, then clipped at 10 m so the
    # whole boundary is covered. The Soil tab statistics stay centre-based and stricter.
    touched = _polygon_mask(geom, rw.transform, raw.shape, rw.crs, all_touched=True)
    valid = touched & np.isfinite(raw) & (raw != nodata) & (raw > 0) & (scaled >= low) & (scaled <= high)
    factor = max(1, round(spec.resolution_m / 10))
    display_values, display_valid, display_transform = _refine(scaled, valid, rw.transform, geom, factor, rw.crs)
    rgba = _rgba_from_classes(display_values, display_valid, spec.breaks, spec.colours)
    filename = f"layer_{spec.layer_id}_{stable_hash([round(value, 6) for value in geom.bounds])}.png"
    _save_png(rgba, filename)
    return _result(
        spec,
        filename,
        _corners(display_transform, display_values.shape[1], display_values.shape[0], rw.crs),
        _legend_for(spec),
        display_valid,
        np.where(display_valid, display_values, np.nan),
    )


def _render_cropland(geom) -> dict[str, Any]:
    from rasterio.windows import Window

    south, west, north, east = geom.bounds[1], geom.bounds[0], geom.bounds[3], geom.bounds[2]
    centre_lat = (south + north) / 2
    centre_lon = (west + east) / 2
    url = landcover_tile(centre_lat, centre_lon)
    with open_raster(remote_cog(url), GDAL_HTTP_TIMEOUT="60") as dataset:
        rw = read_polygon(dataset, geom)
    values = rw.data[0].astype("int64")
    known = np.isin(values, list(LANDCOVER_CLASSES))
    valid = rw.mask & known
    rgba = np.zeros((*values.shape, 4), dtype="uint8")
    present: list[int] = []
    for code in CROPLAND_ORDER:
        selected = valid & (values == code)
        if not selected.any():
            continue
        present.append(code)
        rgba[selected, 0:3] = _hex(LANDCOVER_CLASSES[code]["color"])
        rgba[selected, 3] = 230
    legend = [
        {"color": LANDCOVER_CLASSES[code]["color"], "label": LANDCOVER_CLASSES[code]["name"]}
        for code in present
    ]
    filename = f"layer_cropland_{stable_hash([round(value, 6) for value in geom.bounds])}.png"
    _save_png(rgba, filename)
    spec = LayerSpec(
        layer_id="cropland",
        title="Land cover (2021 baseline)",
        unit="class",
        resolution_m=10,
        breaks=(),
        labels=(),
        colours=(),
        note=CROPLAND_NOTE,
        provider_id="esa_worldcover",
        dataset="ESA WorldCover 2021 v200 (10 m)",
        licence="CC BY 4.0 — © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021)",
        source_url="https://esa-worldcover.org",
    )
    result = {
        "available": True,
        "layer_id": "cropland",
        "title": spec.title,
        "url": f"/api/chips/{filename}",
        "corners": rw.corners_wgs84(),
        "legend": legend,
        "resolution_m": 10,
        "unit": "land-cover class",
        "source": _source(spec),
        "note": CROPLAND_NOTE,
        "pixels": int(valid.sum()),
        "value_range": None,
        "generated_at": iso_now(),
    }
    return result


def _render_terrain(geom, layer_id: str) -> dict[str, Any]:
    spec = SLOPE if layer_id == "slope" else ELEVATION
    elevation, transform, centre_lat, centre_lon = mosaic(geom)
    mask = rasterize(
        [(mapping(geom), 1)],
        out_shape=elevation.shape,
        transform=transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)
    finite = np.isfinite(elevation)
    if layer_id == "slope":
        dx, dy = _ground_pixel_size(centre_lat, centre_lon)
        grad_y, grad_x = np.gradient(np.where(finite, elevation, np.nan))
        values = np.clip(np.sqrt((grad_x / dx) ** 2 + (grad_y / dy) ** 2) * 100.0, 0, 100)
        valid = mask & finite & np.isfinite(values)
    else:
        values = elevation.astype("float64")
        valid = mask & finite
    factor = max(1, round(spec.resolution_m / 10))
    display_values, display_valid, display_transform = _refine(values, valid, transform, geom, factor, "EPSG:4326")
    rgba = _rgba_from_classes(display_values, display_valid, spec.breaks, spec.colours)
    filename = f"layer_{spec.layer_id}_{stable_hash([round(value, 6) for value in geom.bounds])}.png"
    _save_png(rgba, filename)
    corners = _corners(display_transform, display_values.shape[1], display_values.shape[0], "EPSG:4326")
    return _result(spec, filename, corners, _legend_for(spec), display_valid, np.where(display_valid, display_values, np.nan))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def layer_ids() -> list[str]:
    return [*SPECS.keys(), "cropland"]


def render(layer_id: str, geom) -> dict[str, Any]:
    """Render (or reuse) a farm-scale overlay. Returns JSON metadata + chip URL."""
    if layer_id == "evapotranspiration":
        return {
            "available": False,
            "layer_id": layer_id,
            "reason": (
                "FAO WaPOR (evapotranspiration) did not respond at verification time. "
                "No layer is fabricated from another source."
            ),
        }
    geom = ensure_shapely(geom)
    key = f"layer:{layer_id}:{stable_hash([round(value, 6) for value in geom.bounds])}"
    cached = cache_get(key)
    if cached and not cached["expired"]:
        payload = cached["payload"]
        filename = str(payload.get("url", "")).rsplit("/", 1)[-1]
        if filename and (CHIP_DIR / filename).exists():
            payload["cached"] = True
            return payload

    if layer_id in ("soil_ph", "soil_carbon", "soil_texture"):
        payload = _render_soil(SPECS[layer_id], geom)
    elif layer_id == "cropland":
        payload = _render_cropland(geom)
    elif layer_id in ("slope", "elevation"):
        payload = _render_terrain(geom, layer_id)
    else:
        raise KeyError(layer_id)
    cache_put(key, "layers", payload, LAYER_TTL)
    payload["cached"] = False
    return payload
