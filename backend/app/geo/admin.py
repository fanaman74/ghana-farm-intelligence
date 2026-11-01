"""Ghana administrative lookup and agro-ecological zone classification."""

from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from typing import Any

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from ..config import SEED_DIR
from .geodesy import GEOD

log = logging.getLogger(__name__)

# MoFA's agricultural zones with their published rainfall ranges (mm/year).
AGRO_ECOLOGICAL_ZONES: dict[str, dict[str, Any]] = {
    "sudan_savannah": {
        "name": "Sudan Savannah",
        "rainfall_mm": [600, 1000],
        "note": "Hottest and driest zone; single May–October rainy season.",
        "crops": "millet, sorghum, groundnut, cowpea, upland rice, maize (early)",
    },
    "guinea_savannah": {
        "name": "Guinea Savannah",
        "rainfall_mm": [900, 1200],
        "note": "Single rainy season with a pronounced dry season; yam and maize belts.",
        "crops": "yam, maize, groundnut, cowpea, soybean, sorghum, rice",
    },
    "transition": {
        "name": "Transition Zone",
        "rainfall_mm": [1100, 1400],
        "note": "Between forest and savannah; bimodal but the minor season is weaker.",
        "crops": "maize, cassava, plantain, cocoa (marginal), yam",
    },
    "deciduous_forest": {
        "name": "Deciduous Forest",
        "rainfall_mm": [1400, 1800],
        "note": "Bimodal rainfall; the major season drives maize and cocoa.",
        "crops": "cocoa, maize, cassava, plantain, oil palm, cassava, tomato",
    },
    "rain_forest": {
        "name": "Rain Forest",
        "rainfall_mm": [1800, 2400],
        "note": "Highest rainfall; high humidity and fungal pressure.",
        "crops": "cocoa, oil palm, plantain, cassava, rubber (non-food)",
    },
    "coastal_savannah": {
        "name": "Coastal Savannah",
        "rainfall_mm": [700, 1100],
        "note": "Coastal belt with low, unreliable rainfall and sandy soils.",
        "crops": "maize, cassava, onion, shallot, tomato, coconut",
    },
}

# Regions grouped by the zone family used for seasonality.
NORTHERN_REGIONS = {
    "Northern", "Savannah", "North East", "Upper East", "Upper West",
}


@lru_cache(maxsize=1)
def _admin_index() -> dict[str, Any]:
    index_path = SEED_DIR / "ghana_admin.json"
    if not index_path.exists():
        return {"levels": {}, "regions": [], "districts": []}
    return json.loads(index_path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _adm1_layer() -> tuple[list[str], STRtree]:
    path = SEED_DIR / "ghana_adm1.geojson"
    if not path.exists():
        return [], STRtree([])
    payload = json.loads(path.read_text(encoding="utf-8"))
    names, geoms = [], []
    for feature in payload["features"]:
        names.append(feature["properties"]["name"])
        geoms.append(shape(feature["geometry"]))
    return names, STRtree(geoms)


@lru_cache(maxsize=1)
def _adm2_layer() -> tuple[list[str], STRtree]:
    path = SEED_DIR / "ghana_adm2.geojson"
    if not path.exists():
        return [], STRtree([])
    payload = json.loads(path.read_text(encoding="utf-8"))
    names, geoms = [], []
    for feature in payload["features"]:
        names.append(feature["properties"]["name"])
        geoms.append(shape(feature["geometry"]))
    return names, STRtree(geoms)


def _nearest(tree: STRtree, names: list[str], point: Point, max_km: float = 60.0) -> str | None:
    if not names:
        return None
    idx = tree.nearest(point)
    geometry = tree.geometries[idx]
    name = names[idx]
    if point.distance(geometry) == 0:
        return name
    # Approximate km for a distance in degrees at this latitude.
    distance_km = point.distance(geometry) * 111.0
    return name if distance_km <= max_km else None


def locate(lon: float, lat: float) -> dict[str, Any]:
    """Region and district for a coordinate, with an explicit match quality."""
    point = Point(lon, lat)
    region_names, region_tree = _adm1_layer()
    district_names, district_tree = _adm2_layer()
    region = _nearest(region_tree, region_names, point, 80)
    district = _nearest(district_tree, district_names, point, 40)
    quality = "point_in_polygon" if region and district else ("nearest_boundary" if region else "unknown")
    return {
        "region": region,
        "district": district,
        "match": quality,
        "source": "geoBoundaries gbOpen GHA ADM1/ADM2 (16 regions, 260 districts)",
    }


def region_names() -> list[str]:
    return sorted({name for name in _adm1_layer()[0]})


def district_names(region: str | None = None) -> list[dict[str, Any]]:
    names, _tree = _adm2_layer()
    if not region:
        return [{"name": name} for name in sorted(names)]
    adm1_names, adm1_tree = _adm1_layer()
    if region not in adm1_names:
        return [{"name": name} for name in sorted(names)]
    region_geom = adm1_tree.geometries[adm1_names.index(region)]
    payload = json.loads((SEED_DIR / "ghana_adm2.geojson").read_text(encoding="utf-8"))
    output = []
    for feature in payload["features"]:
        geom = shape(feature["geometry"])
        if geom.representative_point().within(region_geom):
            output.append({"name": feature["properties"]["name"], "region": region})
    return sorted(output, key=lambda item: item["name"])


def boundaries(level: str = "ADM2") -> dict[str, Any]:
    filename = {"ADM0": "ghana_adm0.geojson", "ADM1": "ghana_adm1.geojson", "ADM2": "ghana_adm2.geojson"}.get(
        level.upper()
    )
    if not filename:
        raise ValueError(f"Unknown boundary level {level}")
    path = SEED_DIR / filename
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _distance_to_coast_km(lon: float, lat: float) -> float:
    """Rough distance to Ghana's southern coastline (lat ~4.75 at this longitude)."""
    coastline_lat = 4.75 + 0.06 * max(0.0, lon + 3.0)
    return max(0.0, (lat - coastline_lat) * 111.0)


def classify_zone(
    *, annual_rainfall_mm: float | None, region: str | None, lat: float, lon: float
) -> dict[str, Any]:
    """Approximate agro-ecological zone from CHIRPS rainfall, latitude and region.

    Zone boundaries are gradual in reality; this is a planning label with a
    documented method, not a surveyed boundary.
    """
    if annual_rainfall_mm is None:
        if region in NORTHERN_REGIONS:
            zone = "guinea_savannah" if lat > 8.5 else "sudan_savannah"
        elif region in {"Western", "Central", "Greater Accra"}:
            zone = "coastal_savannah"
        else:
            zone = "deciduous_forest"
        method = "region-based fallback (rainfall climatology unavailable)"
        confidence = "low"
    else:
        rainfall = float(annual_rainfall_mm)
        coastal = _distance_to_coast_km(lon, lat) < 45 and lat < 6.3
        if rainfall >= 1800:
            zone = "rain_forest"
        elif coastal and rainfall < 1250:
            zone = "coastal_savannah"
        elif rainfall >= 1450:
            zone = "deciduous_forest"
        elif rainfall >= 1250:
            zone = "transition"
        elif rainfall >= 1020:
            zone = "guinea_savannah"
        else:
            zone = "sudan_savannah"
        method = "CHIRPS 1991–2020 annual rainfall + latitude/coast distance"
        confidence = "medium" if region else "low"

    spec = AGRO_ECOLOGICAL_ZONES[zone]
    return {
        "id": zone,
        "name": spec["name"],
        "rainfall_range_mm": spec["rainfall_mm"],
        "annual_rainfall_mm": round(annual_rainfall_mm, 0) if annual_rainfall_mm else None,
        "season_type": "unimodal" if region in NORTHERN_REGIONS else "bimodal",
        "regions": spec.get("crops"),
        "description": spec["note"],
        "method": method,
        "confidence": confidence,
        "source": "MoFA agro-ecological zone descriptions; rainfall from CHIRPS v2.0 climatology",
        "note": "Zones transition gradually; this label is approximate.",
    }


def point_in_ghana_outline(lon: float, lat: float) -> bool:
    payload = boundaries("ADM0")
    from shapely.geometry import Point as _Point
    from shapely.ops import unary_union

    geoms = [shape(f["geometry"]) for f in payload.get("features", [])]
    if not geoms:
        return True
    return unary_union(geoms).contains(_Point(lon, lat))
