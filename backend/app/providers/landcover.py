"""ESA WorldCover 2021 land-cover context (10 m, historical baseline).

WorldCover is a 2021 baseline, never presented as current crop identification.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..geo.geodesy import ensure_shapely
from ..geo.raster import describe_resolution_deg, open_raster, read_polygon, remote_cog, categorical_fractions
from ..utils import iso_now
from .base import ProviderError, Section, Source, STATUS_OK

PROVIDER = "esa_worldcover"
BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"

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
    urls = {tile_url(lat, lon) for lat in {math.floor(south), math.floor(north)} for lon in {math.floor(west), math.floor(east)}}
    valid = None
    weights = None
    values = None
    transform = None
    crs = None
    pixels = 0

    for url in urls:
        try:
            with open_raster(remote_cog(url), GDAL_HTTP_TIMEOUT="60") as ds:
                rw = read_polygon(ds, geom)
        except Exception:
            continue
        if valid is None:
            values = rw.data[0]
            valid = rw.mask.copy()
            weights = rw.weights
            transform = rw.transform
            crs = rw.crs
            pixels = int(rw.mask.sum())
        else:
            values = np.where(valid, values, rw.data[0])
            valid = valid | rw.mask
            weights = np.where(valid, weights, rw.weights)

    if valid is None or not valid.any():
        raise ProviderError(PROVIDER, "no_tile", "No ESA WorldCover tile could be read for this area")

    class_values = values.astype("int64")
    class_valid = valid & np.isin(class_values, list(CLASSES))
    fractions = categorical_fractions(class_values, class_valid, weights)
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

    resolution = describe_resolution_deg(0.0000833333, 0.0000833333, (south + north) / 2)

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
            "pixels": pixels,
            "source_kind": "satellite_classification_2021",
            "notes": [
                "WorldCover 2021 is a historical baseline, not today's crop identification.",
                "Cropland includes fallow and mixed systems; it says nothing about the current crop.",
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
                method="3°×3° COG window read; polygon mask; area-weighted class fractions",
                limitations="A 2021 baseline. The field may have changed; this is not a current crop map.",
                access_method="AWS Open Data COGs (no key)",
                key_required=False,
            )
        ],
        retrieved_at=iso_now(),
    )
