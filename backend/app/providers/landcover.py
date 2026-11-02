"""ESA WorldCover 2021 land-cover context (10 m, historical baseline).

WorldCover is a 2021 baseline, never presented as current crop identification.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.merge import merge as merge_tiles
from shapely.geometry import mapping

from ..geo.geodesy import ensure_shapely
from ..geo.raster import (
    categorical_fractions,
    describe_resolution_deg,
    gdal_env,
    pixel_weights,
    remote_cog,
)
from ..utils import iso_now
from .base import ProviderError, Section, Source, STATUS_OK

PROVIDER = "esa_worldcover"
BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
# WorldCover v200 tiles are 3 deg x 3 deg at 1/12000 deg per pixel (~10 m).
RES_DEG = 1 / 12000

log = logging.getLogger(__name__)

CLASSES = {
    10: {"name": "Tree cover", "color": "#1a6b3c"},
    20: {"name": "Shrubland", "color": "#8fbc5a"},
    30: {"name": "Grassland", "color": "#d8c86a"},
    40: {"name": "Cropland", "color": "#c9a227"},
    50: {"name": "Built-up", "color": "#9e9e9e"},
    60: {"name": "Bare / sparse", "color": "#b08968"},
    70: {"name": "Snow and ice", "color": "#e8f4f8"},
    80: {"name": "Permanent water", "color": "#2d6da8"},
    90: {"name": "Herbaceous wetland", "color": "#4f8f7a"},
    95: {"name": "Mangroves", "color": "#276b52"},
    100: {"name": "Moss and lichen", "color": "#a8b8a0"},
}


def tile_url(lat: float, lon: float) -> str:
    lat_base = int(math.floor(lat / 3) * 3)
    lon_base = int(math.floor(lon / 3) * 3)
    ns = "N" if lat_base >= 0 else "S"
    ew = "E" if lon_base >= 0 else "W"
    name = f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat_base):02d}{ew}{abs(lon_base):03d}_Map.tif"
    return f"{BASE}/{name}"


def analyse(geom) -> Section:
    geom = ensure_shapely(geom)
    west, south, east, north = geom.bounds
    urls = sorted(
        {
            tile_url(lat, lon)
            for lat in {math.floor(south), math.floor(north)}
            for lon in {math.floor(west), math.floor(east)}
        }
    )

    # Ghana is cut by the 0 degree meridian and by 6/9 N, so a field can span two
    # or more WorldCover tiles. Mosaic them onto one grid: reading each tile into
    # its own window and keeping the first non-empty one silently discarded the
    # classes from every other tile.
    datasets: list[rasterio.DatasetReader] = []
    try:
        with gdal_env(GDAL_HTTP_TIMEOUT="60"):
            for url in urls:
                try:
                    datasets.append(rasterio.open(remote_cog(url)))
                except Exception as exc:  # noqa: BLE001 - a missing tile is reported below
                    log.warning("WorldCover tile %s unreadable: %s", url, exc)
            if not datasets:
                raise ProviderError(PROVIDER, "no_tile", "No ESA WorldCover tile could be read for this area")
            mosaic, transform = merge_tiles(
                datasets,
                bounds=(west, south, east, north),
                res=RES_DEG,
                nodata=0,
                dtype="uint8",
                resampling=Resampling.nearest,
            )
    finally:
        for dataset in datasets:
            dataset.close()

    values = mosaic[0].astype("int64")
    mask = rasterize(
        [(mapping(geom), 1)],
        out_shape=values.shape,
        transform=transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    ).astype(bool)
    weights = pixel_weights(transform, values.shape, True)
    class_valid = mask & np.isin(values, list(CLASSES))
    if not class_valid.any():
        raise ProviderError(PROVIDER, "no_classes", "No classified WorldCover pixel inside this boundary")

    fractions = categorical_fractions(values, class_valid, weights)
    breakdown = [
        {
            "code": int(code),
            "name": CLASSES.get(int(code), {}).get("name", f"Class {code}"),
            "color": CLASSES.get(int(code), {}).get("color", "#888888"),
            "share": share,
        }
        for code, share in fractions.items()
    ]
    crop_share = fractions.get("40", 0.0)
    tree_share = fractions.get("10", 0.0)
    classified_fraction = round(float(class_valid.sum() / max(int(mask.sum()), 1)), 4)

    resolution = describe_resolution_deg(RES_DEG, RES_DEG, (south + north) / 2)

    return Section(
        key="landcover",
        status=STATUS_OK,
        data={
            "classes": breakdown,
            "cropland_share": crop_share,
            "tree_cover_share": tree_share,
            "dominant_class": breakdown[0] if breakdown else None,
            "year": 2021,
            "is_historical_baseline": True,
            "resolution": resolution,
            "pixels": int(class_valid.sum()),
            "classified_fraction": classified_fraction,
            "tiles": {"requested": len(urls), "read": len(datasets)},
            "source_kind": "satellite_classification_2021",
            "notes": [
                "WorldCover 2021 is a historical baseline, not today's crop identification.",
                "Cropland includes fallow and mixed systems; it says nothing about the current crop.",
                "Shares are area-weighted over the classified part of the boundary; "
                f"{classified_fraction * 100:.0f}% of the boundary carried a WorldCover class.",
            ],
        },
        sources=[
            Source(
                provider_id=PROVIDER,
                dataset="ESA WorldCover 2021 v200 (10 m)",
                url="https://esa-worldcover.org",
                licence="CC BY 4.0 — © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021)",
                resolution="10 m",
                period="2021 (historical baseline)",
                method="3°×3° COG tiles mosaicked to one grid; polygon mask; area-weighted class fractions",
                limitations="A 2021 baseline. The field may have changed; this is not a current crop map.",
                access_method="AWS Open Data COGs (no key)",
                key_required=False,
            )
        ],
        retrieved_at=iso_now(),
    )
