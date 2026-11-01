"""Import farm boundaries from GeoJSON, KML or GPX."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from ..geo.geodesy import GeometryError, normalise_geometry


def parse_upload(filename: str, content: bytes) -> dict[str, Any]:
    """Detect the format from the filename or content and return GeoJSON geometry."""
    name = (filename or "").lower()
    text = content.decode("utf-8-sig", errors="replace")
    if name.endswith(".kml") or text.lstrip().startswith("<kml"):
        geometry, warnings, label = parse_kml(text)
        fmt = "KML"
    elif name.endswith(".gpx") or text.lstrip().startswith("<gpx"):
        geometry, warnings, label = parse_gpx(text)
        fmt = "GPX"
    else:
        geometry, warnings, label = parse_geojson(text)
        fmt = "GeoJSON"
    return {"geometry": geometry, "warnings": warnings, "name": label, "format": fmt}


def parse_geojson(text: str) -> tuple[dict[str, Any], list[str], str | None]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeometryError("invalid_geojson", f"The file is not valid GeoJSON: {exc.msg}") from exc
    label = None
    if isinstance(payload, dict) and payload.get("type") == "Feature":
        label = (payload.get("properties") or {}).get("name")
    geom, warnings = normalise_geometry(payload)
    return _geometry_dict(geom), warnings, label


def parse_kml(text: str) -> tuple[dict[str, Any], list[str], str | None]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise GeometryError("invalid_kml", f"This KML file could not be read: {exc}") from exc

    def local(tag: str) -> str:
        return tag.split("}")[-1]

    label = None
    rings: list[list[list[float]]] = []
    for element in root.iter():
        tag = local(element.tag)
        if tag == "name" and label is None and element.text:
            label = element.text.strip()
        if tag == "coordinates" and element.text:
            ring = _parse_coordinate_string(element.text)
            if len(ring) >= 3:
                rings.append(ring)
        if tag == "coord" and element.text:
            parts = element.text.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    rings.append([[float(parts[0]), float(parts[1])]])
                except ValueError:
                    continue
    # gx:Track coordinates arrive as separate <gx:coord> points.
    tracks = [ring for ring in rings if len(ring) == 1]
    polygons = [ring for ring in rings if len(ring) >= 4]
    warnings: list[str] = []
    geometry: dict[str, Any] | None = None
    if polygons:
        outer = max(polygons, key=len)
        if outer[0] != outer[-1]:
            outer.append(outer[0])
            warnings.append("ring_closed_from_kml")
        geometry = {"type": "Polygon", "coordinates": [outer]}
    elif tracks:
        points = [ring[0] for ring in tracks]
        if len(points) >= 3:
            if points[0] != points[-1]:
                points.append(points[0])
                warnings.append("gps_track_closed")
            geometry = {"type": "Polygon", "coordinates": [points]}
    if geometry is None:
        raise GeometryError("no_polygon_in_kml", "No closed polygon was found in this KML file.")
    normalised, extra = normalise_geometry(geometry)
    return _geometry_dict(normalised), warnings + extra, label


def _parse_coordinate_string(text: str) -> list[list[float]]:
    points = []
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        points.append([round(lon, 7), round(lat, 7)])
    return points


def parse_gpx(text: str) -> tuple[dict[str, Any], list[str], str | None]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise GeometryError("invalid_gpx", f"This GPX file could not be read: {exc}") from exc
    points: list[list[float]] = []
    label = None
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag == "trk" and label is None:
            name_node = next((child for child in element if child.tag.split("}")[-1] == "name"), None)
            if name_node is not None and name_node.text:
                label = name_node.text.strip()
        if tag in ("trkpt", "rtept", "wpt"):
            lat, lon = element.get("lat"), element.get("lon")
            if lat and lon:
                try:
                    points.append([round(float(lon), 7), round(float(lat), 7)])
                except ValueError:
                    continue
    if len(points) < 3:
        raise GeometryError("too_few_points", "The GPS file contains fewer than 3 points.")
    warnings = []
    if points[0] != points[-1]:
        points.append(points[0])
        warnings.append("gps_track_closed")
    geometry = {"type": "Polygon", "coordinates": [points]}
    normalised, extra = normalise_geometry(geometry)
    return _geometry_dict(normalised), warnings + extra, label


def _geometry_dict(geom) -> dict[str, Any]:
    from shapely.geometry import mapping

    payload = mapping(geom)
    return {"type": payload["type"], "coordinates": _round(payload["coordinates"])}


def _round(value):
    if isinstance(value, (list, tuple)):
        return [_round(item) for item in value]
    if isinstance(value, float):
        return round(value, 7)
    return value
