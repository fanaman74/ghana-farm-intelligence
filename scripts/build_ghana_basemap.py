#!/usr/bin/env python3
"""Build the self-hosted Ghana basemap seed from the Geofabrik OSM extract.

Input : backend/data/raw/ghana-latest.osm.pbf  (download with --download)
Output: backend/data/seed/ghana_places.json.gz
        backend/data/seed/ghana_roads.geojson.gz
        backend/data/seed/ghana_water.geojson.gz

The script is offline once the .osm.pbf is present.  It is re-runnable: all
outputs are rewritten atomically and gzip headers use mtime=0 so the bytes are
stable for a given input + generated_at.

Data (c) OpenStreetMap contributors, ODbL 1.0.
No raster/vector tiles are downloaded or packaged here.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import osmium
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
PBF_PATH = ROOT / "backend" / "data" / "raw" / "ghana-latest.osm.pbf"
SEED_DIR = ROOT / "backend" / "data" / "seed"
PLACES_OUT = SEED_DIR / "ghana_places.json.gz"
ROADS_OUT = SEED_DIR / "ghana_roads.geojson.gz"
WATER_OUT = SEED_DIR / "ghana_water.geojson.gz"

SOURCE_URL = "https://download.geofabrik.de/africa/ghana.html"
PBF_URL = "https://download.geofabrik.de/africa/ghana-latest.osm.pbf"
SOURCE = "OpenStreetMap via Geofabrik (ODbL 1.0)"
LICENCE = "ODbL 1.0 - (c) OpenStreetMap contributors"
USER_AGENT = "Gh-farmers-basemap-builder/1.0 (+https://github.com/gh-farmers; data build)"

# minlat, minlon, maxlat, maxlon (task-defined Ghana envelope)
BBOX = (4.5, -3.3, 11.2, 1.3)
BBOX_GEOM = box(BBOX[1], BBOX[0], BBOX[3], BBOX[2])

PLACE_TYPES = {
    "city",
    "town",
    "village",
    "hamlet",
    "suburb",
    "neighbourhood",
    "locality",
    "isolated_dwelling",
    "farm",
}

ROAD_CLASSES = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}

# Ghana has 16 regions.  OSM spells them "<Name> Region"; neighbouring
# countries also have admin_level=4 relations inside the Geofabrik extract
# (the extract keeps complete border relations), so a name whitelist is used
# instead of a bbox test alone.
GHANA_REGIONS = {
    "Ahafo",
    "Ashanti",
    "Bono",
    "Bono East",
    "Central",
    "Eastern",
    "Greater Accra",
    "North East",
    "Northern",
    "Oti",
    "Savannah",
    "Upper East",
    "Upper West",
    "Volta",
    "Western",
    "Western North",
}

ROAD_SIMPLIFY_DEG = 0.0005       # ~55 m
ROAD_MIN_LENGTH_M = 200.0
WATER_SIMPLIFY_DEG = 0.0005
WATER_MIN_AREA_M2 = 1_000_000.0  # 1 km^2
RIVER_MIN_LENGTH_M = 10_000.0    # keep unnamed rivers only when long
ROAD_GZ_LIMIT = 10 * 1024 * 1024
WATER_GZ_LIMIT = 3 * 1024 * 1024
PLACES_GZ_LIMIT = 8 * 1024 * 1024

COORD_NDIGITS = 5
PLACE_NDIGITS = 6

T0 = time.time()


def log(message: str) -> None:
    print(f"[{time.time() - T0:6.1f}s] {message}", flush=True)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def clean_text(value: Any) -> str | None:
    """Trim, collapse whitespace and drop empty/non-string tag values."""
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value).replace("\u00a0", " ")).strip()
    return text or None


def norm_name(value: str) -> str:
    return clean_text(value).casefold() if value else ""


def canonical_region(name: str | None) -> str | None:
    if not name:
        return None
    base = re.sub(r"\s+region$", "", clean_text(name) or "", flags=re.IGNORECASE).strip()
    for region in GHANA_REGIONS:
        if base.casefold() == region.casefold():
            return region
    return None


def parse_population(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def in_bbox(lat: float, lon: float) -> bool:
    return (
        math.isfinite(lat)
        and math.isfinite(lon)
        and BBOX[0] <= lat <= BBOX[2]
        and BBOX[1] <= lon <= BBOX[3]
    )


def round_point(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, PLACE_NDIGITS), round(lon, PLACE_NDIGITS)


def gzip_json_bytes(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)


def write_gz_atomic(path: Path, payload: dict[str, Any]) -> int:
    data = gzip_json_bytes(payload)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return len(data)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def projected_coords(coords: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """(lat, lon) -> local metres (x=lon, y=lat) around the first point."""
    lat0 = coords[0][0]
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    return [(lon * kx, lat * ky) for lat, lon in coords]


def line_length_m(coords: Sequence[tuple[float, float]]) -> float:
    if len(coords) < 2:
        return 0.0
    lat0 = sum(c[0] for c in coords) / len(coords)
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    total = 0.0
    prev = coords[0]
    for cur in coords[1:]:
        total += math.hypot((cur[1] - prev[1]) * kx, (cur[0] - prev[0]) * ky)
        prev = cur
    return total


def ring_area_m2(coords: Sequence[tuple[float, float]]) -> float:
    """Shoelace area in m^2 for a (lat, lon) ring."""
    if len(coords) < 4:
        return 0.0
    lat0 = sum(c[0] for c in coords) / len(coords)
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    pts = [(lon * kx, lat * ky) for lat, lon in coords]
    area = 0.0
    prev = pts[0]
    for cur in pts[1:]:
        area += prev[0] * cur[1] - cur[0] * prev[1]
        prev = cur
    return abs(area) / 2.0


def geom_area_m2(geom: Any) -> float:
    if geom.is_empty:
        return 0.0
    polys: list[Polygon] = []
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    total = 0.0
    for poly in polys:
        lat0 = poly.centroid.y
        kx = 111_320.0 * math.cos(math.radians(lat0))
        ky = 110_540.0
        total += Polygon([(x * kx, y * ky) for x, y in poly.exterior.coords]).area
        for hole in poly.interiors:
            total -= Polygon([(x * kx, y * ky) for x, y in hole.coords]).area
    return abs(total)


def clean_geometry(geom: Any) -> Any:
    """Best-effort valid polygonal geometry."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, (Polygon, MultiPolygon)):
        if not geom.is_valid:
            try:
                geom = geom.buffer(0)
            except Exception:
                return None
        return None if geom.is_empty else geom
    return geom


def polygonal_parts(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for part in geom.geoms:
            out.extend(polygonal_parts(part))
        return out
    return []


def as_multipolygon(geom: Any) -> MultiPolygon | None:
    parts = polygonal_parts(clean_geometry(geom))
    if not parts:
        return None
    return MultiPolygon(parts)


def clip_geometry(geom: Any) -> Any:
    """Clip a geometry to the Ghana envelope; returns None when fully outside."""
    if geom is None or geom.is_empty:
        return None
    if BBOX_GEOM.contains(geom):
        return geom
    try:
        clipped = geom.intersection(BBOX_GEOM)
    except Exception:
        return None
    if clipped.is_empty:
        return None
    return clipped


def to_xy(lat: float, lon: float) -> tuple[float, float]:
    return (lon, lat)


def rounded_xy(coords: Iterable[tuple[float, float]]) -> list[list[float]]:
    """Shapely coords are (x=lon, y=lat); GeoJSON must be [lon, lat]."""
    return [[round(lon, COORD_NDIGITS), round(lat, COORD_NDIGITS)] for lon, lat in coords]


def xy_length_m(coords: Sequence[tuple[float, float]]) -> float:
    """Length in metres for (x=lon, y=lat) coordinates."""
    if len(coords) < 2:
        return 0.0
    lat0 = sum(c[1] for c in coords) / len(coords)
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    total = 0.0
    prev = coords[0]
    for cur in coords[1:]:
        total += math.hypot((cur[0] - prev[0]) * kx, (cur[1] - prev[1]) * ky)
        prev = cur
    return total


def geometry_to_geojson(geom: Any, kind: str) -> dict[str, Any] | None:
    """Convert a clipped/simplified shapely geometry to a GeoJSON geometry dict."""
    if geom is None or geom.is_empty:
        return None
    if kind == "line":
        if isinstance(geom, LineString):
            coords = list(geom.coords)
            if len(coords) < 2:
                return None
            return {"type": "LineString", "coordinates": rounded_xy(coords)}
        lines: list[list[list[float]]] = []
        if isinstance(geom, MultiLineString):
            for part in geom.geoms:
                coords = list(part.coords)
                if len(coords) >= 2:
                    lines.append(rounded_xy(coords))
        elif hasattr(geom, "geoms"):
            for part in geom.geoms:
                sub = geometry_to_geojson(part, "line")
                if sub and sub["type"] == "LineString":
                    lines.append(sub["coordinates"])
                elif sub and sub["type"] == "MultiLineString":
                    lines.extend(sub["coordinates"])
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}
    if kind == "polygon":
        polygons: list[list[list[list[float]]]] = []
        for part in polygonal_parts(geom):
            rings: list[list[list[float]]] = [rounded_xy(part.exterior.coords)]
            for hole in part.interiors:
                rings.append(rounded_xy(hole.coords))
            polygons.append(rings)
        if not polygons:
            return None
        if len(polygons) == 1:
            return {"type": "Polygon", "coordinates": polygons[0]}
        return {"type": "MultiPolygon", "coordinates": polygons}
    raise ValueError(kind)


def geojson_first_coord(feature: dict[str, Any]) -> tuple[float, float]:
    geometry = feature["geometry"]
    coordinates = geometry["coordinates"]
    if geometry["type"] == "LineString":
        return coordinates[0][0], coordinates[0][1]
    if geometry["type"] == "Polygon":
        return coordinates[0][0][0], coordinates[0][0][1]
    if geometry["type"] == "MultiLineString":
        return coordinates[0][0][0], coordinates[0][0][1]
    return coordinates[0][0][0][0], coordinates[0][0][0][1]


def water_sort_key(feature: dict[str, Any]) -> tuple[Any, ...]:
    first = geojson_first_coord(feature)
    name = feature["properties"]["name"]
    return (
        name is None,
        feature["properties"]["kind"],
        name or "",
        first[0],
        first[1],
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_pbf(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    log(f"downloading {PBF_URL}")
    request = urllib.request.Request(PBF_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, open(part, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        copied = 0
        last_report = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            copied += len(chunk)
            if copied - last_report >= 20 * 1024 * 1024:
                last_report = copied
                pct = f"{100 * copied / total:.0f}%" if total else "?"
                log(f"  {copied / 1e6:.0f} MB ({pct})")
    if total and copied != total:
        raise RuntimeError(f"download truncated: got {copied} of {total} bytes")
    os.replace(part, destination)
    log(f"downloaded {destination} ({destination.stat().st_size} bytes)")


# ---------------------------------------------------------------------------
# OSM passes
# ---------------------------------------------------------------------------

def way_coords(way: Any) -> tuple[list[tuple[float, float]], int]:
    """Return ((lat, lon), ...) for a way and the number of invalid nodes."""
    coords: list[tuple[float, float]] = []
    invalid = 0
    for node_ref in way.nodes:
        try:
            location = node_ref.location
            if not location.valid():
                invalid += 1
                continue
            coords.append((location.lat, location.lon))
        except osmium.InvalidLocationError:
            invalid += 1
    return coords, invalid


def collect_relation_targets(pbf_path: Path) -> tuple[dict[int, dict[str, Any]], dict[int, list[tuple[int, str]]]]:
    """First pass over relations only (< 1 s).

    Returns relation metadata plus a way-id -> [(relation id, role)] index so
    the way pass can retain the geometry needed for relation centroids and
    boundary polygons.
    """
    relations: dict[int, dict[str, Any]] = {}
    member_ways: dict[int, list[tuple[int, str]]] = defaultdict(list)
    counts = Counter()

    processor = osmium.FileProcessor(pbf_path, entities=osmium.osm.RELATION)
    for rel in processor:
        tags = rel.tags
        kind: str | None = None
        extra: dict[str, Any] = {}

        boundary = tags.get("boundary")
        admin_level = tags.get("admin_level")
        place = tags.get("place")
        natural = tags.get("natural")
        waterway = tags.get("waterway")

        if boundary == "administrative" and admin_level in ("4", "6") and clean_text(tags.get("name")):
            kind = "admin"
            extra["level"] = int(admin_level)
        elif place in PLACE_TYPES and clean_text(tags.get("name")):
            kind = "place"
            extra["place"] = place
        elif natural == "water":
            kind = "water"
            extra["water_kind"] = "lake"
            extra["named"] = clean_text(tags.get("name")) is not None
        elif waterway == "river":
            kind = "water"
            extra["water_kind"] = "river"
            extra["named"] = clean_text(tags.get("name")) is not None
            extra["intermittent"] = tags.get("intermittent") == "yes"

        if kind is None:
            continue

        ways: list[tuple[str, int]] = []
        nodes: list[tuple[str, int]] = []
        for member in rel.members:
            role = member.role or ""
            if member.type == "w":
                ways.append((role, member.ref))
                member_ways[member.ref].append((rel.id, role))
            elif member.type == "n":
                nodes.append((role, member.ref))

        relations[rel.id] = {
            "id": rel.id,
            "kind": kind,
            "name": clean_text(tags.get("name")),
            "tags": {t.k: t.v for t in tags},
            "ways": ways,
            "nodes": nodes,
            **extra,
        }
        counts[kind] += 1

    log(
        "relation pass: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        + f", member ways indexed={len(member_ways)}"
    )
    return relations, member_ways


def collect_place_nodes(pbf_path: Path) -> list[dict[str, Any]]:
    """Cached-tag pass over place-tagged nodes only (C++ key filter)."""
    places: list[dict[str, Any]] = []
    skipped_outside = 0
    skipped_unnamed = 0
    skipped_type = 0

    processor = osmium.FileProcessor(pbf_path, entities=osmium.osm.NODE).with_filter(
        osmium.filter.KeyFilter("place")
    )
    for node in processor:
        place_type = node.tags.get("place")
        if place_type not in PLACE_TYPES:
            skipped_type += 1
            continue
        name = clean_text(node.tags.get("name"))
        if not name:
            skipped_unnamed += 1
            continue
        if not in_bbox(node.lat, node.lon):
            skipped_outside += 1
            continue
        places.append(
            {
                "id": f"osm:node/{node.id}",
                "name": name,
                "type": place_type,
                "lat": round(node.lat, PLACE_NDIGITS),
                "lon": round(node.lon, PLACE_NDIGITS),
                "tags": {
                    "population": node.tags.get("population"),
                    "is_in": node.tags.get("is_in"),
                    "addr:region": node.tags.get("addr:region"),
                    "addr:district": node.tags.get("addr:district"),
                },
            }
        )

    log(
        f"place-node pass: kept={len(places)} (unnamed={skipped_unnamed}, "
        f"other place value={skipped_type}, outside bbox={skipped_outside})"
    )
    return places


def build_way_features(
    pbf_path: Path,
    member_way_ids: set[int],
    relations: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Single pass over ways with node locations.

    Collects roads, water features, place=* way centroids, and the geometry of
    relation member ways (admin boundaries, multipolygon water, place areas).
    """
    roads: list[dict[str, Any]] = []
    water_features: list[dict[str, Any]] = []
    place_ways: list[dict[str, Any]] = []
    road_lines: list[dict[str, Any]] = []
    way_geoms: dict[int, tuple[tuple[float, float], ...]] = {}
    way_ends: dict[int, tuple[int, int]] = {}

    stats = Counter()
    road_classes = Counter()
    water_kinds = Counter()
    invalid_ways = 0

    processor = (
        osmium.FileProcessor(pbf_path)
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
    )

    def add_water_polygon(coords: list[tuple[float, float]], tags: Any, osm_id: str) -> None:
        if len(coords) < 4:
            stats["water_way_too_short"] += 1
            return
        if (coords[0][0], coords[0][1]) != (coords[-1][0], coords[-1][1]):
            stats["water_way_not_closed"] += 1
            return
        area = ring_area_m2(coords)
        if area < WATER_MIN_AREA_M2:
            stats["water_way_too_small"] += 1
            return
        name = clean_text(tags.get("name"))
        reservoir = (
            tags.get("water") == "reservoir"
            or tags.get("landuse") == "reservoir"
            or (name is not None and re.search(r"reservoir|dam", name, re.IGNORECASE) is not None)
        )
        geom = Polygon([to_xy(lat, lon) for lat, lon in coords])
        geom = clean_geometry(geom)
        geom = geom.simplify(WATER_SIMPLIFY_DEG, preserve_topology=True) if geom else None
        geom = clip_geometry(geom)
        geojson = geometry_to_geojson(geom, "polygon") if geom else None
        if geojson is None:
            stats["water_way_clipped_empty"] += 1
            return
        kind = "reservoir" if reservoir else "lake"
        water_features.append(
            {
                "type": "Feature",
                "properties": {"kind": kind, "name": name},
                "geometry": geojson,
            }
        )
        water_kinds[kind] += 1

    def add_river(coords: list[tuple[float, float]], tags: Any) -> None:
        name = clean_text(tags.get("name"))
        length = line_length_m(coords)
        if not name and length < RIVER_MIN_LENGTH_M:
            stats["river_unnamed_short"] += 1
            return
        geom = LineString([to_xy(lat, lon) for lat, lon in coords])
        geom = geom.simplify(WATER_SIMPLIFY_DEG, preserve_topology=True)
        geom = clip_geometry(geom)
        geojson = geometry_to_geojson(geom, "line") if geom else None
        if geojson is None:
            stats["river_clipped_empty"] += 1
            return
        water_features.append(
            {
                "type": "Feature",
                "properties": {"kind": "river", "name": name},
                "geometry": geojson,
            }
        )
        water_kinds["river"] += 1

    for way in processor:
        stats["ways"] += 1
        if stats["ways"] % 500_000 == 0:
            log(f"  way pass: {stats['ways']:,} ways, road segments={len(road_lines):,}, water={len(water_features):,}")

        way_id = way.id
        tags = way.tags
        is_member = way_id in member_way_ids

        highway = tags.get("highway")
        natural = tags.get("natural")
        waterway = tags.get("waterway")
        place_type = tags.get("place")

        wanted = is_member or highway in ROAD_CLASSES or natural == "water" or waterway == "river"
        if place_type in PLACE_TYPES and clean_text(tags.get("name")):
            wanted = True
        if not wanted:
            continue

        coords, invalid = way_coords(way)
        if invalid:
            invalid_ways += 1
        if len(coords) < 2:
            stats["way_no_geometry"] += 1
            continue

        if is_member:
            way_geoms[way_id] = tuple(coords)
            way_ends[way_id] = (
                way.nodes[0].ref,
                way.nodes[-1].ref,
            )

        if highway in ROAD_CLASSES:
            road_lines.append(
                {
                    "properties": {
                        "class": highway,
                        "name": clean_text(tags.get("name")),
                        "ref": clean_text(tags.get("ref")),
                        "surface": clean_text(tags.get("surface")),
                    },
                    "geometry": LineString([to_xy(lat, lon) for lat, lon in coords]),
                }
            )
            road_classes[highway] += 1

        if natural == "water":
            add_water_polygon(coords, tags, f"way/{way_id}")

        if waterway == "river":
            add_river(coords, tags)

        if place_type in PLACE_TYPES:
            name = clean_text(tags.get("name"))
            if name:
                lat = sum(c[0] for c in coords) / len(coords)
                lon = sum(c[1] for c in coords) / len(coords)
                if in_bbox(lat, lon):
                    place_ways.append(
                        {
                            "id": f"osm:way/{way_id}",
                            "name": name,
                            "type": place_type,
                            "lat": round(lat, PLACE_NDIGITS),
                            "lon": round(lon, PLACE_NDIGITS),
                            "tags": {
                                "population": tags.get("population"),
                                "is_in": tags.get("is_in"),
                                "addr:region": tags.get("addr:region"),
                                "addr:district": tags.get("addr:district"),
                            },
                        }
                    )
                else:
                    stats["place_way_outside_bbox"] += 1

    log(
        f"way pass: {stats['ways']:,} ways, road segments collected={len(road_lines):,}, "
        f"water={len(water_features):,}, place ways={len(place_ways)}, "
        f"relation member ways with geometry={len(way_geoms)}"
    )
    if invalid_ways:
        log(f"  warning: {invalid_ways} ways had missing node locations (skipped nodes)")
    for key in sorted(stats):
        if key not in {"ways"} and stats[key]:
            log(f"  {key}: {stats[key]:,}")
    log("  road segments by class: " + ", ".join(f"{k}={v}" for k, v in road_classes.most_common()))
    log("  water by kind: " + ", ".join(f"{k}={v}" for k, v in water_kinds.most_common()))

    roads, road_stats = assemble_roads(road_lines)
    log(
        f"road assembly: {len(road_lines):,} segments -> {len(roads):,} features "
        f"(merged groups={road_stats['groups']:,}, dropped short/clipped={road_stats['dropped']:,})"
    )

    return {
        "roads": roads,
        "water": water_features,
        "place_ways": place_ways,
        "way_geoms": way_geoms,
        "way_ends": way_ends,
        "road_classes": dict(road_classes),
        "stats": stats,
    }


def assemble_roads(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter]:
    """Merge contiguous OSM ways with identical tags, then simplify/clip.

    OSM splits a road into many short ways at junctions.  Merging first keeps
    the network connected and prevents the 200 m cut from deleting thousands
    of junction stubs.  Ways only merge when they share endpoints, so roads
    with the same name in different cities stay separate.
    """
    stats = Counter()
    groups: dict[tuple[str, str, str, str], list[LineString]] = defaultdict(list)
    for record in segments:
        props = record["properties"]
        key = (props["class"], props["name"] or "", props["ref"] or "", props["surface"] or "")
        groups[key].append(record["geometry"])
    stats["groups"] = len(groups)

    features: list[dict[str, Any]] = []
    for key in sorted(groups):
        class_name, name, ref, surface = key
        lines = groups[key]
        if len(lines) == 1:
            parts: list[Any] = [lines[0]]
        else:
            try:
                merged = linemerge(MultiLineString(lines))
            except Exception:
                merged = MultiLineString(lines)
            if isinstance(merged, LineString):
                parts = [merged]
            elif isinstance(merged, MultiLineString):
                parts = list(merged.geoms)
            else:
                parts = [part for part in getattr(merged, "geoms", []) if isinstance(part, LineString)]

        for part in parts:
            if part.is_empty or len(part.coords) < 2:
                continue
            geom = part.simplify(ROAD_SIMPLIFY_DEG, preserve_topology=True)
            if geom.is_empty:
                continue
            geom = clip_geometry(geom)
            if geom is None:
                stats["dropped"] += 1
                continue
            if xy_length_m(list(geom.coords)) < ROAD_MIN_LENGTH_M:
                stats["dropped"] += 1
                continue
            geojson = geometry_to_geojson(geom, "line")
            if geojson is None:
                stats["dropped"] += 1
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "class": class_name,
                        "name": name or None,
                        "ref": ref or None,
                        "surface": surface or None,
                    },
                    "geometry": geojson,
                }
            )

    features.sort(
        key=lambda f: (
            f["properties"]["class"],
            f["properties"]["name"] or "",
            f["properties"]["ref"] or "",
            geojson_first_coord(f),
        )
    )
    return features, stats


# ---------------------------------------------------------------------------
# Relation -> polygon assembly
# ---------------------------------------------------------------------------

def stitch_rings(
    segments: list[dict[str, Any]],
) -> tuple[list[tuple[str, list[tuple[float, float]]]], int]:
    """Join member ways end-to-end into closed rings using shared node refs.

    Returns ([(role, [(lat, lon), ...]), ...], number_of_open_chains).
    """
    by_start: dict[int, list[int]] = defaultdict(list)
    by_end: dict[int, list[int]] = defaultdict(list)
    for index, segment in enumerate(segments):
        by_start[segment["start"]].append(index)
        by_end[segment["end"]].append(index)

    unused = set(range(len(segments)))
    rings: list[tuple[str, list[tuple[float, float]]]] = []
    open_chains = 0

    while unused:
        index = unused.pop()
        segment = segments[index]
        role = segment["role"]
        ring = list(segment["coords"])
        first_ref = segment["start"]
        current_ref = segment["end"]
        closed = current_ref == first_ref and len(ring) >= 4

        while not closed:
            next_index: int | None = None
            reverse = False
            for candidate in by_start.get(current_ref, ()):
                if candidate in unused:
                    next_index = candidate
                    break
            if next_index is None:
                for candidate in by_end.get(current_ref, ()):
                    if candidate in unused:
                        next_index = candidate
                        reverse = True
                        break
            if next_index is None:
                break
            unused.remove(next_index)
            nxt = segments[next_index]
            coords = list(nxt["coords"])
            if reverse:
                coords.reverse()
                current_ref = nxt["start"]
            else:
                current_ref = nxt["end"]
            ring.extend(coords[1:])
            if current_ref == first_ref:
                closed = True

        if closed and len(ring) >= 4:
            rings.append((role, ring))
        elif len(ring) >= 4 and line_length_m([ring[0], ring[-1]]) < 1_000.0:
            # small gap (usually a clipped border fragment): close it
            rings.append((role, ring))
            open_chains += 1
        else:
            open_chains += 1

    return rings, open_chains


def polygons_from_relation(
    relation: dict[str, Any],
    way_geoms: dict[int, tuple[tuple[float, float], ...]],
    way_ends: dict[int, tuple[int, int]],
) -> tuple[MultiPolygon | None, int]:
    """Assemble a (multi)polygon from a boundary/multipolygon relation."""
    segments: list[dict[str, Any]] = []
    for role, way_id in relation["ways"]:
        coords = way_geoms.get(way_id)
        ends = way_ends.get(way_id)
        if not coords or not ends or len(coords) < 2:
            continue
        segments.append(
            {
                "role": "inner" if role == "inner" else "outer",
                "start": ends[0],
                "end": ends[1],
                "coords": coords,
            }
        )
    if not segments:
        return None, 0

    rings, open_chains = stitch_rings(segments)
    outers = [coords for role, coords in rings if role == "outer"]
    inners = [coords for role, coords in rings if role == "inner"]
    polygons: list[Polygon] = []
    for outer in outers:
        if len(outer) < 4:
            continue
        try:
            shell = Polygon([to_xy(lat, lon) for lat, lon in outer])
        except Exception:
            continue
        holes: list[list[tuple[float, float]]] = []
        for inner in inners:
            try:
                if shell.contains(Point(to_xy(inner[0][0], inner[0][1]))):
                    holes.append([to_xy(lat, lon) for lat, lon in inner])
            except Exception:
                continue
        try:
            poly = Polygon(shell.exterior, holes)
        except Exception:
            poly = shell
        poly = clean_geometry(poly)
        if poly is not None:
            polygons.extend(polygonal_parts(poly))

    if not polygons:
        return None, open_chains
    return MultiPolygon(polygons) if len(polygons) > 1 else polygons[0], open_chains


def representative_point(geom: Any) -> tuple[float, float] | None:
    if geom is None or geom.is_empty:
        return None
    point = geom.centroid
    if not geom.contains(point):
        point = geom.representative_point()
    lat, lon = point.y, point.x
    if not in_bbox(lat, lon):
        return None
    return round(lat, PLACE_NDIGITS), round(lon, PLACE_NDIGITS)


# ---------------------------------------------------------------------------
# Region / district context
# ---------------------------------------------------------------------------

def build_admin_context(
    relations: dict[int, dict[str, Any]],
    way_geoms: dict[int, tuple[tuple[float, float], ...]],
    way_ends: dict[int, tuple[int, int]],
) -> dict[str, Any]:
    region_entries: list[dict[str, Any]] = []
    district_entries: list[dict[str, Any]] = []
    unassembled = 0
    open_chains_total = 0

    for rel in relations.values():
        if rel["kind"] != "admin":
            continue
        multipolygon, open_chains = polygons_from_relation(rel, way_geoms, way_ends)
        open_chains_total += open_chains
        if multipolygon is None:
            unassembled += 1
            if rel["level"] == 6:
                continue
            # region without an assembled polygon: add centre from member coords
            coords = [c for role, wid in rel["ways"] if (g := way_geoms.get(wid)) for c in g]
            if not coords:
                continue
            region = canonical_region(rel["name"])
            if region is None:
                continue
            lat = sum(c[0] for c in coords) / len(coords)
            lon = sum(c[1] for c in coords) / len(coords)
            if in_bbox(lat, lon):
                region_entries.append({"name": region, "geometry": None, "centre": (lat, lon), "id": rel["id"]})
            continue

        centre = representative_point(multipolygon)
        if rel["level"] == 4:
            region = canonical_region(rel["name"])
            if region is None:
                continue
            region_entries.append({"name": region, "geometry": multipolygon, "centre": centre, "id": rel["id"]})
        else:
            district_entries.append({"name": rel["name"], "geometry": multipolygon, "centre": centre, "id": rel["id"]})

    log(
        f"admin polygons: regions={len(region_entries)}, districts assembled={len(district_entries)}, "
        f"unassembled={unassembled}, open/gap rings={open_chains_total}"
    )

    # Keep only districts whose centre falls inside a Ghanaian region polygon.
    region_geoms = [entry["geometry"] for entry in region_entries if entry["geometry"] is not None]
    region_names = [entry["name"] for entry in region_entries if entry["geometry"] is not None]
    region_tree = STRtree(region_geoms) if region_geoms else None

    def region_at(lon: float, lat: float) -> tuple[str | None, bool]:
        if region_tree is None:
            return None, False
        point = Point(lon, lat)
        hits = region_tree.query(point)
        best_name = None
        best_area = None
        for index in hits:
            geom = region_geoms[int(index)]
            if not geom.contains(point):
                continue
            area = geom.area
            if best_area is None or area < best_area:
                best_area = area
                best_name = region_names[int(index)]
        if best_name is not None:
            return best_name, True
        return None, False

    ghana_districts: list[dict[str, Any]] = []
    for entry in district_entries:
        if entry["centre"] is None:
            continue
        lat, lon = entry["centre"]
        region, inside = region_at(lon, lat)
        if not inside:
            continue
        entry["region"] = region
        ghana_districts.append(entry)

    log(f"admin polygons: Ghana districts kept={len(ghana_districts)} (foreign/unmatched dropped)")

    return {
        "regions": [e for e in region_entries if e["geometry"] is not None or e["centre"] is not None],
        "districts": ghana_districts,
        "region_geoms": region_geoms,
        "region_names": region_names,
        "region_tree": region_tree,
        "region_at": region_at,
    }


def district_lookup(districts: list[dict[str, Any]]) -> tuple[list[Any], list[str], STRtree | None]:
    geoms = [entry["geometry"] for entry in districts if entry["geometry"] is not None]
    names = [entry["name"] for entry in districts if entry["geometry"] is not None]
    tree = STRtree(geoms) if geoms else None
    return geoms, names, tree


def find_district(
    tree: STRtree | None,
    geoms: list[Any],
    names: list[str],
    lon: float,
    lat: float,
) -> str | None:
    if tree is None:
        return None
    point = Point(lon, lat)
    best_name = None
    best_area = None
    for index in tree.query(point):
        geom = geoms[int(index)]
        if not geom.contains(point):
            continue
        area = geom.area
        if best_area is None or area < best_area:
            best_area = area
            best_name = names[int(index)]
    return best_name


# ---------------------------------------------------------------------------
# Places assembly
# ---------------------------------------------------------------------------

def build_places(
    node_places: list[dict[str, Any]],
    way_places: list[dict[str, Any]],
    relations: dict[int, dict[str, Any]],
    way_geoms: dict[int, tuple[tuple[float, float], ...]],
    admin: dict[str, Any],
) -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []

    def add_place(record: dict[str, Any]) -> None:
        tags = record.get("tags", {})
        lat = float(record["lat"])
        lon = float(record["lon"])
        region, _ = admin["region_at"](lon, lat)
        district = find_district(admin["district_tree"], admin["district_geoms"], admin["district_names"], lon, lat)

        addr_region = clean_text(tags.get("addr:region"))
        addr_district = clean_text(tags.get("addr:district"))
        vicinity = clean_text(tags.get("is_in"))
        if region is None and addr_region:
            region = addr_region
        if district is None and addr_district:
            district = addr_district

        places.append(
            {
                "id": record["id"],
                "name": record["name"],
                "type": record["type"],
                "lat": round(lat, PLACE_NDIGITS),
                "lon": round(lon, PLACE_NDIGITS),
                "region": region,
                "district": district,
                "population": parse_population(tags.get("population")),
                "vicinity": vicinity,
                "addr_region": addr_region,
                "addr_district": addr_district,
            }
        )

    for record in node_places:
        add_place(record)
    for record in way_places:
        add_place(record)

    place_relation_count = 0
    for rel in relations.values():
        if rel["kind"] != "place":
            continue
        coords = [c for role, way_id in rel["ways"] if (geom := way_geoms.get(way_id)) for c in geom]
        if not coords:
            continue
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)
        if not in_bbox(lat, lon):
            continue
        add_place(
            {
                "id": f"osm:relation/{rel['id']}",
                "name": rel["name"],
                "type": rel["place"],
                "lat": round(lat, PLACE_NDIGITS),
                "lon": round(lon, PLACE_NDIGITS),
                "tags": {
                    "population": rel["tags"].get("population"),
                    "is_in": rel["tags"].get("is_in"),
                    "addr:region": rel["tags"].get("addr:region"),
                    "addr:district": rel["tags"].get("addr:district"),
                },
            }
        )
        place_relation_count += 1

    log(f"places: nodes+ways={len(node_places) + len(way_places)}, place relations={place_relation_count}")

    # --- administrative centres -------------------------------------------
    existing_names = {norm_name(p["name"]) for p in places}
    existing_keys = {(norm_name(p["name"]), round(p["lat"], 2), round(p["lon"], 2)) for p in places}

    def near_same_name(name: str, lat: float, lon: float) -> bool:
        if norm_name(name) not in existing_names:
            return False
        for p in places:
            if norm_name(p["name"]) != norm_name(name):
                continue
            if abs(p["lat"] - lat) <= 0.05 and abs(p["lon"] - lon) <= 0.05:
                return True
        return False

    centre_count = 0
    for entry in admin["regions"]:
        centre = entry.get("centre")
        if centre is None:
            continue
        lat, lon = centre
        if not in_bbox(lat, lon):
            continue
        name = entry["name"]
        if near_same_name(name, lat, lon):
            continue
        key = (norm_name(name), round(lat, 2), round(lon, 2))
        if key in existing_keys:
            continue
        region = name
        district = find_district(admin["district_tree"], admin["district_geoms"], admin["district_names"], lon, lat)
        places.append(
            {
                "id": f"osm:relation/{entry['id']}",
                "name": name,
                "type": "region_centre",
                "lat": round(lat, PLACE_NDIGITS),
                "lon": round(lon, PLACE_NDIGITS),
                "region": region,
                "district": district,
                "population": None,
                "vicinity": None,
                "addr_region": None,
                "addr_district": None,
            }
        )
        existing_keys.add(key)
        centre_count += 1

    for entry in admin["districts"]:
        centre = entry.get("centre")
        if centre is None:
            continue
        lat, lon = centre
        if not in_bbox(lat, lon):
            continue
        name = entry["name"]
        if near_same_name(name, lat, lon):
            continue
        key = (norm_name(name), round(lat, 2), round(lon, 2))
        if key in existing_keys:
            continue
        places.append(
            {
                "id": f"osm:relation/{entry['id']}",
                "name": name,
                "type": "district_centre",
                "lat": round(lat, PLACE_NDIGITS),
                "lon": round(lon, PLACE_NDIGITS),
                "region": entry.get("region"),
                "district": name,
                "population": None,
                "vicinity": None,
                "addr_region": None,
                "addr_district": None,
            }
        )
        existing_keys.add(key)
        centre_count += 1

    log(f"places: admin centres added={centre_count} (region_centre/district_centre)")

    places.sort(key=lambda p: (p["name"].casefold(), p["id"]))
    return places


# ---------------------------------------------------------------------------
# Water relations
# ---------------------------------------------------------------------------

def add_water_relations(
    relations: dict[int, dict[str, Any]],
    way_geoms: dict[int, tuple[tuple[float, float], ...]],
    way_ends: dict[int, tuple[int, int]],
    water_features: list[dict[str, Any]],
) -> None:
    added = Counter()
    for rel in relations.values():
        if rel["kind"] != "water" or not rel.get("named"):
            # unnamed water relations are redundant with their member ways
            continue

        if rel["water_kind"] == "river":
            segments = [
                LineString([to_xy(lat, lon) for lat, lon in way_geoms[way_id]])
                for role, way_id in rel["ways"]
                if way_geoms.get(way_id)
            ]
            if not segments:
                continue
            try:
                merged = unary_union(segments)
            except Exception:
                merged = MultiLineString(segments)
            merged = merged.simplify(WATER_SIMPLIFY_DEG, preserve_topology=True)
            merged = clip_geometry(merged)
            geojson = geometry_to_geojson(merged, "line") if merged else None
            if geojson is None:
                continue
            water_features.append(
                {
                    "type": "Feature",
                    "properties": {"kind": "river", "name": rel["name"]},
                    "geometry": geojson,
                }
            )
            added["river"] += 1
            continue

        multipolygon, _ = polygons_from_relation(rel, way_geoms, way_ends)
        if multipolygon is None:
            continue
        if geom_area_m2(multipolygon) < WATER_MIN_AREA_M2:
            continue
        geom = clean_geometry(multipolygon)
        geom = geom.simplify(WATER_SIMPLIFY_DEG, preserve_topology=True) if geom else None
        geom = clip_geometry(geom)
        geojson = geometry_to_geojson(geom, "polygon") if geom else None
        if geojson is None:
            continue
        name = rel["name"]
        reservoir = (
            rel["tags"].get("water") == "reservoir"
            or rel["tags"].get("landuse") == "reservoir"
            or (name is not None and re.search(r"reservoir|dam", name, re.IGNORECASE) is not None)
        )
        kind = "reservoir" if reservoir else "lake"
        water_features.append(
            {
                "type": "Feature",
                "properties": {"kind": kind, "name": name},
                "geometry": geojson,
            }
        )
        added[kind] += 1

    log("water relations added: " + (", ".join(f"{k}={v}" for k, v in added.items()) or "none"))


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------

def feature_collection(
    features: list[dict[str, Any]],
    generated_at: str,
    counts: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "generated_at": generated_at,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "licence": LICENCE,
        "counts": counts,
    }
    if extra:
        properties.update(extra)
    return {"type": "FeatureCollection", "properties": properties, "features": features}


def build_roads_payload(roads: list[dict[str, Any]], generated_at: str) -> tuple[dict[str, Any], str]:
    policies: list[tuple[str, set[str] | None, float | None]] = [
        ("keep all classes", None, None),
        ("drop tertiary_link", {"tertiary_link"}, None),
        ("drop tertiary_link + tertiary", {"tertiary_link", "tertiary"}, None),
    ]
    chosen_note = policies[0][0]
    payload: dict[str, Any] = {}
    for note, dropped, _ in policies:
        selected = roads if not dropped else [f for f in roads if f["properties"]["class"] not in dropped]
        by_class = Counter(f["properties"]["class"] for f in selected)
        payload = feature_collection(
            selected,
            generated_at,
            {"features": len(selected), "by_class": dict(sorted(by_class.items()))},
        )
        size = len(gzip_json_bytes(payload))
        log(f"roads candidate ({note}): {len(selected):,} features, {size / 1e6:.2f} MB gzip")
        if size <= ROAD_GZ_LIMIT:
            chosen_note = note
            break
    else:
        # Last resort: coarser coordinates are not re-simplified here; note it.
        chosen_note = "keep all classes (over size limit)"
    return payload, chosen_note


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Ghana basemap seed from Geofabrik PBF")
    parser.add_argument("--pbf", type=Path, default=PBF_PATH, help="input .osm.pbf")
    parser.add_argument("--out-dir", type=Path, default=SEED_DIR, help="output seed directory")
    parser.add_argument("--download", action="store_true", help="download the PBF if missing")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    pbf_path: Path = args.pbf
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if not pbf_path.exists() or pbf_path.stat().st_size < 50 * 1024 * 1024:
        if args.download:
            download_pbf(pbf_path)
        else:
            raise SystemExit(
                f"missing input: {pbf_path}\n"
                f"download it once with:\n  python scripts/build_ghana_basemap.py --download\n"
                f"or fetch {PBF_URL}"
            )

    stat = pbf_path.stat()
    log(f"input: {pbf_path} ({stat.st_size:,} bytes, mtime={datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    log(f"generated_at = {generated_at}")

    # 1. relations (fast, members index)
    relations, member_way_index = collect_relation_targets(pbf_path)
    member_way_ids = set(member_way_index)

    # 2. place nodes (C++ filtered)
    node_places = collect_place_nodes(pbf_path)

    # 3. ways (locations enabled, nodes filtered from Python callbacks)
    way_result = build_way_features(pbf_path, member_way_ids, relations)
    way_geoms = way_result["way_geoms"]
    way_ends = way_result["way_ends"]

    # 4. admin polygons / regions / districts
    admin = build_admin_context(relations, way_geoms, way_ends)
    admin["district_geoms"], admin["district_names"], admin["district_tree"] = district_lookup(admin["districts"])

    # 5. water relations (Lake Volta etc.)
    water_features = way_result["water"]
    add_water_relations(relations, way_geoms, way_ends, water_features)
    water_features.sort(key=water_sort_key)

    # 6. places
    places = build_places(node_places, way_result["place_ways"], relations, way_geoms, admin)

    # 7. write places
    places_payload = {
        "generated_at": generated_at,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "licence": LICENCE,
        "bbox": {"min_lat": BBOX[0], "min_lon": BBOX[1], "max_lat": BBOX[2], "max_lon": BBOX[3]},
        "counts": {
            "total": len(places),
            "by_type": dict(sorted(Counter(p["type"] for p in places).items())),
        },
        "places": places,
    }
    places_size = write_gz_atomic(out_dir / PLACES_OUT.name, places_payload)
    log(f"wrote {out_dir / PLACES_OUT.name} ({places_size:,} bytes gz, {len(places):,} places)")

    # 8. write roads
    roads_payload, road_policy = build_roads_payload(way_result["roads"], generated_at)
    roads_size = write_gz_atomic(out_dir / ROADS_OUT.name, roads_payload)
    log(f"wrote {out_dir / ROADS_OUT.name} ({roads_size:,} bytes gz, {len(roads_payload['features']):,} features)")

    # 9. write water
    water_kinds = Counter(f["properties"]["kind"] for f in water_features)
    water_payload = feature_collection(
        water_features,
        generated_at,
        {"features": len(water_features), "by_kind": dict(sorted(water_kinds.items()))},
    )
    water_size = write_gz_atomic(out_dir / WATER_OUT.name, water_payload)
    log(f"wrote {out_dir / WATER_OUT.name} ({water_size:,} bytes gz, {len(water_features):,} features)")

    # 10. verification (reload from disk)
    ok = verify_outputs(out_dir / PLACES_OUT.name, out_dir / ROADS_OUT.name, out_dir / WATER_OUT.name)
    log(f"road policy: {road_policy}")
    log("done" if ok else "done with verification warnings")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _iter_geojson_coords(geometry: dict[str, Any]) -> Iterator[tuple[float, float]]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point":
        yield float(coords[0]), float(coords[1])
    elif gtype in ("LineString", "MultiPoint"):
        for lon, lat in coords:
            yield float(lon), float(lat)
    elif gtype in ("Polygon", "MultiLineString"):
        for part in coords:
            for lon, lat in part:
                yield float(lon), float(lat)
    elif gtype == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                for lon, lat in ring:
                    yield float(lon), float(lat)


def _bbox_of(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    first = True
    min_lat = min_lon = float("inf")
    max_lat = max_lon = float("-inf")
    for lon, lat in points:
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        if first:
            min_lat = max_lat = lat
            min_lon = max_lon = lon
            first = False
        else:
            min_lat = min(min_lat, lat)
            max_lat = max(max_lat, lat)
            min_lon = min(min_lon, lon)
            max_lon = max(max_lon, lon)
    if first:
        return None
    return min_lat, min_lon, max_lat, max_lon


def verify_outputs(places_path: Path, roads_path: Path, water_path: Path) -> bool:
    ok = True
    log("verification ------------------------------------------------")

    def load(path: Path) -> dict[str, Any]:
        return json.loads(gzip.decompress(path.read_bytes()))

    # places
    payload = load(places_path)
    places = payload["places"]
    points = [(p["lon"], p["lat"]) for p in places]
    bbox = _bbox_of(points)
    bad = [p for p in places if not in_bbox(p["lat"], p["lon"]) or not (math.isfinite(p["lat"]) and math.isfinite(p["lon"]))]
    print(f"places: size={places_path.stat().st_size:,} bytes, count={len(places)}, bbox={bbox}")
    print("places samples:")
    for sample in places[:3]:
        print("  " + json.dumps(sample, ensure_ascii=False))
    print(f"places out-of-bbox/non-finite={len(bad)}")
    if bad or bbox is None or not (BBOX[0] <= bbox[0] and BBOX[2] >= bbox[2]):
        ok = False

    # roads
    payload = load(roads_path)
    features = payload["features"]
    points = (pt for f in features for pt in _iter_geojson_coords(f["geometry"]))
    bbox = _bbox_of(points)
    print(
        f"roads: size={roads_path.stat().st_size:,} bytes, count={len(features)}, bbox={bbox}, "
        f"classes={payload['properties']['counts'].get('by_class')}"
    )
    print("roads samples:")
    for sample in features[:3]:
        text = json.dumps(sample, ensure_ascii=False)
        print("  " + (text[:300] + "..." if len(text) > 300 else text))
    bad_roads = 0
    for feature in features:
        for lon, lat in _iter_geojson_coords(feature["geometry"]):
            if not (math.isfinite(lat) and math.isfinite(lon) and in_bbox(lat, lon)):
                bad_roads += 1
                break
    print(f"roads out-of-bbox/non-finite features={bad_roads}")
    if bad_roads:
        ok = False

    # water
    payload = load(water_path)
    features = payload["features"]
    points = (pt for f in features for pt in _iter_geojson_coords(f["geometry"]))
    bbox = _bbox_of(points)
    print(
        f"water: size={water_path.stat().st_size:,} bytes, count={len(features)}, bbox={bbox}, "
        f"kinds={payload['properties']['counts'].get('by_kind')}"
    )
    print("water samples:")
    for sample in features[:3]:
        text = json.dumps(sample, ensure_ascii=False)
        print("  " + (text[:300] + "..." if len(text) > 300 else text))
    bad_water = 0
    for feature in features:
        for lon, lat in _iter_geojson_coords(feature["geometry"]):
            if not (math.isfinite(lat) and math.isfinite(lon) and in_bbox(lat, lon)):
                bad_water += 1
                break
    print(f"water out-of-bbox/non-finite features={bad_water}")
    if bad_water:
        ok = False

    log("verification " + ("OK" if ok else "FAILED"))
    return ok


if __name__ == "__main__":
    sys.exit(main())
