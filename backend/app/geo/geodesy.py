"""Geodesic geometry: validation, area, perimeter, centroids, splitting.

Area and perimeter are computed on the WGS84 ellipsoid with pyproj's ``Geod``,
not in degrees and not with a planar approximation, so a farm polygon reports
its true geodesic area in hectares.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import shapely
from pyproj import Geod, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Polygon,
    box,
    mapping,
    shape,
)
from shapely.ops import split as shapely_split
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union
from shapely.validation import explain_validity

from ..config import GHANA_BOUNDS, SEED_DIR, settings
from ..utils import stable_hash

GEOD = Geod(ellps="WGS84")
EQUAL_AREA = "EPSG:6933"
_TO_EQUAL_AREA = Transformer.from_crs("EPSG:4326", EQUAL_AREA, always_xy=True)
_FROM_EQUAL_AREA = Transformer.from_crs(EQUAL_AREA, "EPSG:4326", always_xy=True)


class GeometryError(ValueError):
    """A user-facing geometry validation failure."""

    def __init__(self, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


@dataclass
class GeometryInfo:
    geometry: dict
    area_ha: float
    perimeter_m: float
    centroid_lat: float
    centroid_lon: float
    bbox: dict[str, float]
    vertex_count: int
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "geometry": self.geometry,
            "area_ha": self.area_ha,
            "area_acres": round(self.area_ha * 2.4710538, 4),
            "perimeter_m": self.perimeter_m,
            "centroid": {"lat": self.centroid_lat, "lon": self.centroid_lon},
            "bbox": self.bbox,
            "vertex_count": self.vertex_count,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# Coordinate helpers
# --------------------------------------------------------------------------- #
def round_coords(value: Any, digits: int = 7) -> Any:
    if isinstance(value, (list, tuple)):
        return [round_coords(v, digits) for v in value]
    if isinstance(value, float):
        return round(value, digits)
    return value


def _raw_rings(raw: Any):
    """Yield coordinate rings from raw GeoJSON without constructing geometry."""
    if not isinstance(raw, dict):
        return
    kind = raw.get("type")
    if kind == "Feature" and isinstance(raw.get("geometry"), dict):
        yield from _raw_rings(raw["geometry"])
    elif kind == "GeometryCollection":
        for geometry in raw.get("geometries") or []:
            yield from _raw_rings(geometry)
    elif kind == "Polygon":
        for ring in raw.get("coordinates") or []:
            if isinstance(ring, list):
                yield ring
    elif kind == "MultiPolygon":
        for polygon in raw.get("coordinates") or []:
            for ring in polygon or []:
                if isinstance(ring, list):
                    yield ring


def _coerce_polygonal(geom: Any) -> Polygon | MultiPolygon:
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, GeometryCollection):
        polygons = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if not polygons:
            raise GeometryError("unsupported_geometry", "The geometry contains no polygon.")
        merged = unary_union(polygons)
        if not isinstance(merged, (Polygon, MultiPolygon)):
            raise GeometryError("unsupported_geometry", "The geometry could not be interpreted as a polygon.")
        return merged
    raise GeometryError(
        "unsupported_geometry",
        f"Farm boundaries must be polygons, not {geom.geom_type}.",
    )


def normalise_geometry(
    raw: Any, *, repair: bool = False
) -> tuple[Polygon | MultiPolygon, list[str]]:
    """Accept GeoJSON geometry/Feature/FeatureCollection and return a polygon.

    Returns the geometry plus a list of human-readable adjustments that were
    applied (ring closure, duplicate vertices, repair).
    """
    warnings: list[str] = []
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        # Shapely silently closes rings; report it so the user knows we adjusted input.
        for ring in _raw_rings(raw):
            if ring and ring[0] != ring[-1]:
                warnings.append("ring_closed_automatically")
                break
    if isinstance(raw, dict) and raw.get("type") == "FeatureCollection":
        features = raw.get("features") or []
        geoms = []
        for feature in features:
            if feature.get("geometry"):
                geoms.append(shape(feature["geometry"]))
        if not geoms:
            raise GeometryError("empty_geometry", "No geometry found in the FeatureCollection.")
        geom: Any = unary_union(geoms)
        warnings.append("multiple_features_merged")
    elif isinstance(raw, dict) and raw.get("type") == "Feature":
        geom = shape(raw["geometry"])
    elif isinstance(raw, dict):
        geom = shape(raw)
    elif isinstance(raw, BaseGeometry):
        geom = raw
    else:
        raise GeometryError("unsupported_geometry", "Unsupported geometry input.")

    geom = _coerce_polygonal(geom)

    # Close rings explicitly and report it.
    polygons: list[Polygon] = []
    for polygon in _iter_polygons(geom):
        coords = list(polygon.exterior.coords)
        if len(coords) < 3:
            raise GeometryError("too_few_vertices", "A polygon needs at least 3 distinct vertices.")
        if coords[0] != coords[-1]:
            coords.append(coords[0])
            warnings.append("ring_closed_automatically")
        cleaned = [coords[0]]
        for point in coords[1:]:
            if point != cleaned[-1]:
                cleaned.append(point)
        if len(cleaned) < 4:
            raise GeometryError("too_few_vertices", "A polygon needs at least 3 distinct vertices.")
        if len(cleaned) != len(coords):
            warnings.append("duplicate_vertices_removed")
        interiors = []
        for ring in polygon.interiors:
            inner = list(ring.coords)
            if inner and inner[0] != inner[-1]:
                inner.append(inner[0])
            if len(inner) >= 4:
                interiors.append(inner)
        polygons.append(Polygon(cleaned, interiors))

    merged = unary_union(polygons) if len(polygons) > 1 else polygons[0]
    merged = _coerce_polygonal(merged)
    if not merged.is_valid:
        if not repair:
            reason = explain_validity(merged)
            raise GeometryError(
                "self_intersection",
                "The boundary crosses itself. Fix the highlighted vertices and try again.",
                reason,
            )
        repaired = shapely.make_valid(merged)
        merged = _coerce_polygonal(repaired)
        warnings.append("geometry_repaired")
    if merged.is_empty:
        raise GeometryError("empty_geometry", "The boundary encloses no area.")
    return merged, warnings


def _iter_polygons(geom: Polygon | MultiPolygon):
    if isinstance(geom, Polygon):
        yield geom
    else:
        for part in geom.geoms:
            if isinstance(part, Polygon):
                yield part


def ensure_shapely(geom: Any) -> Polygon | MultiPolygon:
    """Accept a shapely geometry, GeoJSON dict or JSON string."""
    if isinstance(geom, BaseGeometry):
        return _coerce_polygonal(geom)
    normalised, _ = normalise_geometry(geom)
    return normalised


def polygon_hash(raw: Any, digits: int = 6) -> str:
    """Stable identity for a boundary, derived from its rings.

    Cache keys and overlay filenames must never identify a polygon by its
    bounding box: two different fields can share one (a diagonal split of a
    rectangle gives two triangles with identical bboxes) and would then be
    served each other's soil, rainfall and imagery.
    """
    geom = ensure_shapely(raw)
    payload = mapping(geom)
    return stable_hash([geom.geom_type, round_coords(payload["coordinates"], digits)])


# --------------------------------------------------------------------------- #
# Measurements
# --------------------------------------------------------------------------- #
def geodesic_area_ha(geom: Polygon | MultiPolygon) -> float:
    area, _ = GEOD.geometry_area_perimeter(geom)
    return abs(area) / 10_000.0


def geodesic_area_m2(geom: Polygon | MultiPolygon) -> float:
    area, _ = GEOD.geometry_area_perimeter(geom)
    return abs(area)


def geodesic_perimeter_m(geom: Polygon | MultiPolygon) -> float:
    _, perimeter = GEOD.geometry_area_perimeter(geom)
    return abs(perimeter)


def centroid_lonlat(geom: Polygon | MultiPolygon) -> tuple[float, float]:
    """Area-weighted centroid, computed in an equal-area projection."""
    projected = shapely_transform(_TO_EQUAL_AREA.transform, geom)
    point = projected.centroid
    lon, lat = _FROM_EQUAL_AREA.transform(point.x, point.y)
    return float(lat), float(lon)


def bbox_lonlat(geom: Polygon | MultiPolygon) -> dict[str, float]:
    minx, miny, maxx, maxy = geom.bounds
    return {"west": minx, "south": miny, "east": maxx, "north": maxy}


def vertex_count(geom: Polygon | MultiPolygon) -> int:
    total = 0
    for polygon in _iter_polygons(geom):
        total += max(0, len(polygon.exterior.coords) - 1)
    return total


@lru_cache(maxsize=1)
def ghana_outline() -> Polygon | MultiPolygon | None:
    """Optional national outline used for a stricter inside-Ghana test."""
    for candidate in (
        SEED_DIR / "ghana_adm0.geojson",
        settings.data_dir / "seed" / "ghana_adm0.geojson",
    ):
        if candidate.exists():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                features = payload.get("features", [payload])
                geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
                if not geoms:
                    continue
                return unary_union(geoms)
            except (OSError, ValueError, KeyError):
                continue
    return None


@lru_cache(maxsize=4)
def _buffered_outline(buffer_deg: float) -> Polygon | MultiPolygon | None:
    """The national outline, buffered once instead of once per vertex.

    ``is_in_ghana`` used to call ``outline.buffer()`` for every vertex, which is
    thousands of full-geometry buffers on an imported boundary.
    """
    outline = ghana_outline()
    if outline is None:
        return None
    return outline.buffer(buffer_deg)


def is_in_ghana(lon: float, lat: float, buffer_deg: float = 0.02) -> bool:
    if not (
        GHANA_BOUNDS["min_lat"] - buffer_deg <= lat <= GHANA_BOUNDS["max_lat"] + buffer_deg
        and GHANA_BOUNDS["min_lon"] - buffer_deg <= lon <= GHANA_BOUNDS["max_lon"] + buffer_deg
    ):
        return False
    buffered = _buffered_outline(buffer_deg)
    if buffered is None:
        return True
    from shapely.geometry import Point

    return buffered.contains(Point(lon, lat))


def validate_geometry(
    raw: Any, *, repair: bool = False, require_ghana: bool = True
) -> GeometryInfo:
    """Full validation used by the API before anything is stored or analysed."""
    geom, warnings = normalise_geometry(raw, repair=repair)

    area_ha = geodesic_area_ha(geom)
    perimeter = geodesic_perimeter_m(geom)

    if area_ha < settings.min_area_ha:
        raise GeometryError(
            "area_too_small",
            f"This boundary is only {area_ha * 10_000:.0f} m². Draw an area of at least "
            f"{settings.min_area_ha * 10_000:.0f} m² (0.005 ha).",
            {"area_ha": area_ha},
        )
    if area_ha > settings.max_area_ha:
        raise GeometryError(
            "area_too_large",
            f"This boundary is {area_ha:,.0f} ha, larger than the {settings.max_area_ha:,.0f} ha limit.",
            {"area_ha": area_ha},
        )

    if require_ghana:
        outside = [pt for pt in _all_points(geom) if not is_in_ghana(pt[0], pt[1])]
        if outside:
            raise GeometryError(
                "outside_ghana",
                "Part of this boundary falls outside Ghana. Farm Intelligence only covers Ghana.",
                {"points_outside": len(outside), "example": {"lon": outside[0][0], "lat": outside[0][1]}},
            )

    if area_ha > 2_000:
        warnings.append("large_boundary_check_it_is_the_intended_farm")
    if isinstance(geom, MultiPolygon) and len(geom.geoms) > 1:
        warnings.append("multi_part_boundary")
    if perimeter > 0 and area_ha > 0:
        # Very elongated shapes suggest an accidental tap chain.
        compactness = (4 * math.pi * geodesic_area_m2(geom)) / (perimeter**2)
        if compactness < 0.02 and area_ha < 5:
            warnings.append("unusually_long_thin_boundary")

    lat, lon = centroid_lonlat(geom)
    rounded = {
        "type": geom.geom_type,
        "coordinates": round_coords(mapping(geom)["coordinates"]),
    }
    return GeometryInfo(
        geometry=rounded,
        area_ha=round(area_ha, 4),
        perimeter_m=round(perimeter, 1),
        centroid_lat=round(lat, 7),
        centroid_lon=round(lon, 7),
        bbox={key: round(value, 7) for key, value in bbox_lonlat(geom).items()},
        vertex_count=vertex_count(geom),
        warnings=warnings,
    )


def _all_points(geom: Polygon | MultiPolygon):
    for polygon in _iter_polygons(geom):
        for lon, lat in polygon.exterior.coords:
            yield lon, lat


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
def split_polygon(geom: Polygon | MultiPolygon, line: LineString) -> list[Polygon]:
    """Split a polygon by a user-drawn line. Returns the resulting parts."""
    if not line.intersects(geom):
        raise GeometryError("split_line_missing", "The split line does not cross this boundary.")
    try:
        parts = shapely_split(geom, line)
    except (shapely.errors.GEOSException, ValueError) as exc:  # pragma: no cover - GEOS specific
        raise GeometryError("split_failed", "The boundary could not be split along that line.") from exc
    polygons = [p for p in parts.geoms if isinstance(p, Polygon) and p.area > 0] if hasattr(parts, "geoms") else []
    if len(polygons) < 2:
        raise GeometryError(
            "split_failed",
            "Draw a line that crosses the boundary completely from one edge to another.",
        )
    return polygons


def overlap_report(
    candidate: Polygon | MultiPolygon, existing: list[tuple[str, str, Any]]
) -> list[dict[str, Any]]:
    """Overlap warnings against other saved farms/fields.

    ``existing`` items are ``(id, name, geometry)``.
    """
    candidate_area = geodesic_area_m2(candidate)
    results: list[dict[str, Any]] = []
    for item_id, name, raw_geom in existing:
        try:
            other, _ = normalise_geometry(raw_geom)
        except GeometryError:
            continue
        if not candidate.intersects(other):
            continue
        intersection = candidate.intersection(other)
        if intersection.is_empty:
            continue
        overlap_m2 = geodesic_area_m2(intersection) if isinstance(intersection, (Polygon, MultiPolygon)) else 0.0
        if overlap_m2 <= 0:
            continue
        results.append(
            {
                "id": item_id,
                "name": name,
                "overlap_ha": round(overlap_m2 / 10_000, 4),
                "overlap_fraction": round(overlap_m2 / candidate_area, 4) if candidate_area else None,
            }
        )
    return results


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def local_metres(geom: Polygon | MultiPolygon, lon: float, lat: float) -> Polygon | MultiPolygon:
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg(lon, lat)}", always_xy=True)
    return shapely_transform(transformer.transform, geom)


def geometry_to_geojson_feature(geom: Polygon | MultiPolygon, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": geom.geom_type, "coordinates": round_coords(mapping(geom)["coordinates"])},
        "properties": properties,
    }


def union_geometries(geometries: list[Any]) -> Polygon | MultiPolygon:
    parsed = [normalise_geometry(g)[0] for g in geometries]
    merged = unary_union(parsed)
    return _coerce_polygonal(merged)


def point_in_geometry(geom: Polygon | MultiPolygon, lon: float, lat: float) -> bool:
    from shapely.geometry import Point

    return geom.contains(Point(lon, lat))
