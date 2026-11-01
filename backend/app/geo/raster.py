"""Windowed raster sampling and area-weighted statistics.

Every provider raster is read *only* for the window that covers the selected
farm polygon, then masked with the polygon itself. Nothing downloads a
continental raster for a small field.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np
import rasterio
from pyproj import Geod, Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds
from shapely.geometry import Polygon, MultiPolygon, mapping
from shapely.ops import transform as shapely_transform

GEOD = Geod(ellps="WGS84")

# Keep GDAL's HTTP behaviour tight for /vsicurl reads from COGs.
GDAL_HTTP_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff,.vrt,.png",
    "GDAL_HTTP_TIMEOUT": "60",
    "VSI_CACHE": "FALSE",
}


@contextmanager
def gdal_env(**extra: str) -> Iterator[None]:
    options = dict(GDAL_HTTP_ENV)
    options.update(extra)
    with rasterio.Env(**options):
        yield


@contextmanager
def open_raster(path: str | Path, **env: str) -> Iterator[rasterio.DatasetReader]:
    with gdal_env(**env):
        with rasterio.open(str(path)) as dataset:
            yield dataset


def is_remote(path: str | Path) -> bool:
    return str(path).startswith("http")


def remote_cog(url: str) -> str:
    """Wrap an HTTPS COG URL for GDAL range reads."""
    return url if url.startswith("/vsicurl/") else "/vsicurl/" + url


# --------------------------------------------------------------------------- #
# Geometry <-> window
# --------------------------------------------------------------------------- #
def project_geometry(geom: Polygon | MultiPolygon, dst_crs: Any) -> Polygon | MultiPolygon:
    transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    projected = shapely_transform(transformer.transform, geom)
    if not projected.is_valid:
        from shapely import make_valid

        projected = make_valid(projected)
    return projected


def polygon_window(
    dataset: rasterio.DatasetReader, geom_wgs84: Polygon | MultiPolygon, pad_pixels: int = 1
) -> tuple[Polygon | MultiPolygon, Window, Any]:
    """Return (projected geometry, integer pixel window, window transform)."""
    geom = project_geometry(geom_wgs84, dataset.crs)
    left, bottom, right, top = geom.bounds
    raw = from_bounds(left, bottom, right, top, transform=dataset.transform)
    col_off = math.floor(raw.col_off) - pad_pixels
    row_off = math.floor(raw.row_off) - pad_pixels
    width = max(1, math.ceil(raw.width) + pad_pixels * 2 + 1)
    height = max(1, math.ceil(raw.height) + pad_pixels * 2 + 1)
    window = Window(col_off, row_off, width, height)
    return geom, window, dataset.window_transform(window)


def pixel_weights(transform: Any, shape: tuple[int, int], geographic: bool) -> np.ndarray:
    """Per-pixel ground area in m² (latitude aware for geographic rasters)."""
    rows, cols = shape
    if not geographic:
        area = abs(transform.a * transform.e - transform.b * transform.d)
        return np.full(shape, area, dtype="float64")

    dlon = abs(transform.a)
    dlat = abs(transform.e)
    lats = transform.f + (np.arange(rows) + 0.5) * transform.e
    lon0 = transform.c + 0.5 * transform.a
    _, _, dx = GEOD.inv(np.full(rows, lon0), lats, np.full(rows, lon0 + dlon), lats)
    _, _, dy = GEOD.inv(np.full(rows, lon0), lats, np.full(rows, lon0), lats + dlat)
    per_row = np.abs(dx * dy)
    return np.repeat(per_row[:, None], cols, axis=1)


@dataclass
class RasterWindow:
    """A raster window clipped to a polygon, with per-pixel area weights."""

    data: np.ndarray  # (bands, rows, cols)
    mask: np.ndarray  # (rows, cols) True inside polygon
    weights: np.ndarray  # (rows, cols) m² per pixel
    transform: Any
    window: Window
    crs: Any
    res_x: float
    res_y: float
    nodata: float | None
    polygon_area_m2: float
    source: str
    dtype: Any

    @property
    def shape(self) -> tuple[int, int]:
        return self.mask.shape

    @property
    def pixel_area_m2(self) -> float:
        return float(np.median(self.weights[self.mask])) if self.mask.any() else float(
            np.median(self.weights)
        )

    @property
    def mask_area_m2(self) -> float:
        return float(self.weights[self.mask].sum())

    @property
    def coverage_fraction(self) -> float:
        """Share of the polygon's true area covered by sampled pixel centres."""
        if self.polygon_area_m2 <= 0:
            return 0.0
        return float(min(1.0, self.mask_area_m2 / self.polygon_area_m2))

    def bounds_wgs84(self) -> dict[str, float]:
        """Geographic bounds of this window (for image overlays)."""
        left, top = self.transform @ (0, 0)
        right, bottom = self.transform @ (self.shape[1], self.shape[0])
        transformer = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        lons, lats = transformer.transform([left, right, right, left], [bottom, bottom, top, top])
        return {
            "west": float(min(lons)),
            "south": float(min(lats)),
            "east": float(max(lons)),
            "north": float(max(lats)),
        }

    def corners_wgs84(self) -> list[list[float]]:
        """Image corner coordinates [TL, TR, BR, BL] for MapLibre image sources."""
        width, height = self.shape[1], self.shape[0]
        pixels = [(0, 0), (width, 0), (width, height), (0, height)]
        transformer = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        output = []
        for col, row in pixels:
            x, y = self.transform @ (col, row)
            lon, lat = transformer.transform(x, y)
            output.append([round(float(lon), 7), round(float(lat), 7)])
        return output


def read_polygon(
    dataset: rasterio.DatasetReader,
    geom_wgs84: Polygon | MultiPolygon,
    *,
    bands: Sequence[int] = (1,),
    resampling: Resampling = Resampling.nearest,
    fill_value: float | None = None,
    polygon_area_m2: float | None = None,
    out_shape: tuple[int, int] | None = None,
    source: str = "raster",
    boundless: bool = True,
) -> RasterWindow:
    """Read the window covering ``geom_wgs84`` and build the polygon mask.

    ``boundless=False`` is required for WarpedVRT datasets, whose windows must be
    clamped to the dataset extent.
    """
    projected, window, win_transform = polygon_window(dataset, geom_wgs84)
    if not boundless:
        col_off = max(0, int(math.floor(window.col_off)))
        row_off = max(0, int(math.floor(window.row_off)))
        col_end = min(dataset.width, int(math.ceil(window.col_off + window.width)))
        row_end = min(dataset.height, int(math.ceil(window.row_off + window.height)))
        window = Window(col_off, row_off, max(1, col_end - col_off), max(1, row_end - row_off))
        win_transform = dataset.window_transform(window)
        read_kwargs: dict[str, Any] = {}
    else:
        read_kwargs = {"boundless": True, "fill_value": fill_value}
    fill = dataset.nodata if fill_value is None and dataset.nodata is not None else (fill_value or 0)
    data = dataset.read(
        list(bands),
        window=window,
        masked=False,
        resampling=resampling,
        out_shape=(len(bands), *out_shape) if out_shape else None,
        **read_kwargs,
    ).astype("float64")

    if out_shape:
        # Window transform for the resampled grid.
        win_transform = win_transform * win_transform.scale(
            window.width / out_shape[1], window.height / out_shape[0]
        )

    mask = rasterize(
        [(mapping(projected), 1)],
        out_shape=data.shape[1:],
        transform=win_transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)

    if not mask.any():
        # Sub-pixel polygon: fall back to the pixel containing the centroid.
        centre = project_geometry(geom_wgs84.centroid, dataset.crs)
        col, row = ~win_transform @ (centre.x, centre.y)
        row = int(min(max(int(row), 0), data.shape[1] - 1))
        col = int(min(max(int(col), 0), data.shape[2] - 1))
        mask[row, col] = True

    return RasterWindow(
        data=data,
        mask=mask,
        weights=pixel_weights(win_transform, mask.shape, dataset.crs.is_geographic),
        transform=win_transform,
        window=window,
        crs=dataset.crs,
        res_x=abs(win_transform.a),
        res_y=abs(win_transform.e),
        nodata=fill,
        polygon_area_m2=(
            polygon_area_m2
            if polygon_area_m2 is not None
            else abs(GEOD.geometry_area_perimeter(geom_wgs84)[0])
        ),
        source=source,
        dtype=dataset.dtypes[0] if dataset.dtypes else "float64",
    )


def _polygon_area(geom: Polygon | MultiPolygon) -> float:
    if geom.is_empty:
        return 0.0
    if geom.geom_type == "Polygon":
        return float(geom.area)
    return float(sum(part.area for part in geom.geoms))


def align_to_grid(
    values: np.ndarray,
    src_transform: Any,
    src_crs: Any,
    dst_transform: Any,
    dst_crs: Any,
    dst_shape: tuple[int, int],
    *,
    categorical: bool = True,
) -> np.ndarray:
    """Nearest-neighbour reproject/resample of a categorical band to another grid."""
    out = np.zeros(dst_shape, dtype=values.dtype)
    reproject(
        source=values,
        destination=out,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.nearest if categorical else Resampling.bilinear,
        src_nodata=0,
        dst_nodata=0,
    )
    return out


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def weighted_quantile(values: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    order = np.argsort(values)
    sorted_values = values[order]
    sorted_weights = weights[order]
    cumulative = np.cumsum(sorted_weights) - 0.5 * sorted_weights
    total = sorted_weights.sum()
    if total <= 0:
        return float("nan")
    cumulative = cumulative / total
    return float(np.interp(quantile, cumulative, sorted_values))


def summarise(
    values: np.ndarray,
    valid: np.ndarray,
    weights: np.ndarray,
    *,
    with_sum: bool = False,
    unit_scale: float = 1.0,
    decimals: int = 4,
) -> dict[str, Any]:
    """Area-weighted summary statistics over valid pixels inside the polygon."""
    selected_values = values[valid]
    selected_weights = weights[valid]
    if selected_values.size == 0:
        return {
            "status": "no_valid_pixels",
            "valid_pixels": 0,
            "valid_fraction": 0.0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "sd": None,
            "p10": None,
            "p90": None,
            "sum": None,
            "pixel_count": 0,
        }
    scaled = selected_values * unit_scale
    mean = float(np.average(scaled, weights=selected_weights))
    variance = float(np.average((scaled - mean) ** 2, weights=selected_weights))
    result = {
        "status": "ok",
        "valid_pixels": int(selected_values.size),
        "valid_fraction": None,  # filled by caller with polygon context
        "mean": round(mean, decimals),
        "median": round(weighted_quantile(scaled, selected_weights, 0.5), decimals),
        "min": round(float(np.min(scaled)), decimals),
        "max": round(float(np.max(scaled)), decimals),
        "sd": round(math.sqrt(max(variance, 0.0)), decimals),
        "p10": round(weighted_quantile(scaled, selected_weights, 0.10), decimals),
        "p90": round(weighted_quantile(scaled, selected_weights, 0.90), decimals),
        "sum": round(float(np.sum(scaled)), decimals) if with_sum else None,
        "pixel_count": int(selected_values.size),
    }
    return result


def valid_fraction(rw: RasterWindow, valid_mask: np.ndarray) -> float:
    total = rw.weights[rw.mask].sum()
    if total <= 0:
        return 0.0
    return float(min(1.0, rw.weights[valid_mask & rw.mask].sum() / total))


def categorical_fractions(
    values: np.ndarray, valid: np.ndarray, weights: np.ndarray
) -> dict[str, float]:
    selected_values = values[valid].astype("int64")
    selected_weights = weights[valid]
    if selected_values.size == 0:
        return {}
    total = selected_weights.sum()
    result: dict[str, float] = {}
    for code in np.unique(selected_values):
        share = float(selected_weights[selected_values == code].sum() / total)
        result[str(int(code))] = round(share, 4)
    return dict(sorted(result.items(), key=lambda item: item[1], reverse=True))


def area_weighted_valid_area(rw: RasterWindow, valid: np.ndarray) -> float:
    return float(rw.weights[valid & rw.mask].sum())


def describe_resolution(rw: RasterWindow) -> dict[str, Any]:
    """Resolution expressed honestly, in metres, for the UI and provenance."""
    if rw.crs.is_geographic:
        centre_lat = rw.bounds_wgs84()["south"] + 0.5 * (rw.bounds_wgs84()["north"] - rw.bounds_wgs84()["south"])
        return describe_resolution_deg(rw.res_x, rw.res_y, centre_lat)
    return {
        "native": [round(rw.res_x, 3), round(rw.res_y, 3)],
        "native_unit": "metres",
        "approx_m": [round(rw.res_x, 1), round(rw.res_y, 1)],
        "nominal_m": round(max(rw.res_x, rw.res_y), 1),
    }


def describe_resolution_deg(res_x: float, res_y: float, centre_lat: float) -> dict[str, Any]:
    """Resolution for a geographic grid, reported in native degrees and metres."""
    approx_y = abs(res_y) * 111_320
    approx_x = abs(res_x) * 111_320 * math.cos(math.radians(centre_lat))
    return {
        "native": [round(abs(res_x), 8), round(abs(res_y), 8)],
        "native_unit": "degrees",
        "approx_m": [round(approx_x, 1), round(approx_y, 1)],
        "nominal_m": round(max(approx_x, approx_y), 1),
    }


def sub_field_detail_supported(
    polygon_area_m2: float, pixel_area_m2: float, min_pixels: float = 4.0
) -> bool:
    """Whether the pixel grid can honestly show within-field variation."""
    if pixel_area_m2 <= 0:
        return False
    return polygon_area_m2 / pixel_area_m2 >= min_pixels


# --------------------------------------------------------------------------- #
# Map chips
# --------------------------------------------------------------------------- #
def _palette_ndvi(stops: list[tuple[float, tuple[int, int, int]]], value: float) -> tuple[int, int, int]:
    for (pos_a, col_a), (pos_b, col_b) in zip(stops, stops[1:]):
        if pos_a <= value <= pos_b:
            span = pos_b - pos_a or 1e-9
            t = (value - pos_a) / span
            return tuple(int(round(col_a[i] + (col_b[i] - col_a[i]) * t)) for i in range(3))
    return stops[-1][1] if value > stops[-1][0] else stops[0][1]


NDVI_STOPS = [
    (-0.2, (140, 81, 10)),
    (0.15, (191, 129, 45)),
    (0.3, (223, 194, 125)),
    (0.45, (225, 225, 160)),
    (0.6, (128, 205, 193)),
    (0.75, (1, 102, 94)),
    (0.95, (0, 60, 48)),
]
NDMI_STOPS = [
    (-0.4, (140, 81, 10)),
    (-0.1, (191, 129, 45)),
    (0.1, (223, 194, 125)),
    (0.3, (128, 205, 193)),
    (0.5, (1, 102, 94)),
]


def colour_array(
    values: np.ndarray,
    valid: np.ndarray,
    palette: str = "ndvi",
    vmin: float | None = None,
    vmax: float | None = None,
) -> np.ndarray:
    stops = NDVI_STOPS if palette == "ndvi" else NDMI_STOPS
    lo = stops[0][0] if vmin is None else vmin
    hi = stops[-1][0] if vmax is None else vmax
    finite = np.isfinite(values) & valid
    rgba = np.zeros((*values.shape, 4), dtype="uint8")
    if not finite.any():
        return rgba
    normalised = np.clip((values - lo) / (hi - lo), 0.0, 1.0)
    lookup = np.array(
        [_palette_ndvi(stops, lo + t * (hi - lo)) for t in np.linspace(0, 1, 256)],
        dtype="uint8",
    )
    idx = (normalised * 255).astype("uint8")
    rgb = lookup[idx]
    rgba[..., :3] = rgb
    rgba[..., 3] = np.where(finite, 235, 0).astype("uint8")
    return rgba


def true_colour(
    rgb: np.ndarray, valid: np.ndarray, gamma: float = 1.0 / 1.35
) -> np.ndarray:
    """Percentile-stretched true colour RGBA from three float bands (0-10000 ref)."""
    out = np.zeros((*valid.shape, 4), dtype="uint8")
    if not valid.any():
        return out
    channels = []
    for band in rgb:
        values = band[valid]
        low, high = np.percentile(values, [2, 98])
        if high <= low:
            high = low + 1
        scaled = np.clip((band - low) / (high - low), 0, 1) ** gamma
        channels.append((scaled * 255).astype("uint8"))
    out[..., 0] = channels[0]
    out[..., 1] = channels[1]
    out[..., 2] = channels[2]
    out[..., 3] = np.where(valid, 255, 0).astype("uint8")
    return out


def save_png(rgba: np.ndarray, path: Path) -> str:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG", optimize=True)
    return str(path)
