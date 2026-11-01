"""Local Ghana locality search backed by the bundled OSM place index."""

from __future__ import annotations

from typing import Any

from ..db import get_conn
from .admin import _adm2_layer, district_names, locate, region_names
from ..config import GHANA_BOUNDS


def search(query: str, *, limit: int = 12) -> dict[str, Any]:
    """Search localities, districts and regions. Works offline."""
    text = (query or "").strip()
    if len(text) < 2:
        return {"query": text, "results": []}
    like = f"%{text.lower()}%"
    startswith = f"{text.lower()}%"
    results: list[dict[str, Any]] = []

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id,name,type,lat,lon,region,district,population
            FROM place_index
            WHERE search_text LIKE ? OR name LIKE ?
            ORDER BY
              CASE WHEN lower(name) = ? THEN 0
                   WHEN lower(name) LIKE ? THEN 1
                   ELSE 2 END,
              COALESCE(population, 0) DESC,
              length(name) ASC
            LIMIT ?
            """,
            (like, like, text.lower(), startswith, limit),
        ).fetchall()
        for row in rows:
            results.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "type": row["type"],
                    "lat": row["lat"],
                    "lon": row["lon"],
                    "region": row["region"],
                    "district": row["district"],
                    "population": row["population"],
                    "kind": "place",
                }
            )

    # Regions and districts are searchable too, even if no OSM node exists.
    if len(results) < limit:
        lowered = text.lower()
        for region in region_names():
            if lowered in region.lower():
                results.append(
                    {
                        "id": f"region:{region}",
                        "name": region,
                        "type": "region",
                        "region": region,
                        "district": None,
                        "kind": "region",
                        "lat": None,
                        "lon": None,
                    }
                )
                break
    if len(results) < limit:
        lowered = text.lower()
        for district in district_names():
            if lowered in district["name"].lower():
                results.append(
                    {
                        "id": f"district:{district['name']}",
                        "name": district["name"],
                        "type": "district",
                        "region": district.get("region"),
                        "district": district["name"],
                        "kind": "district",
                        "lat": None,
                        "lon": None,
                    }
                )
                if len(results) >= limit:
                    break

    # Attach a centroid for region/district entries so the map can fly there.
    for item in results:
        if item.get("lat") is None and item["kind"] in ("region", "district"):
            centroid = _admin_centroid(item["kind"], item["name"])
            if centroid:
                item["lat"], item["lon"] = centroid

    return {"query": text, "count": len(results), "results": results[:limit]}


def _admin_centroid(kind: str, name: str) -> tuple[float, float] | None:
    import json

    from shapely.geometry import shape

    from ..config import SEED_DIR

    filename = "ghana_adm1.geojson" if kind == "region" else "ghana_adm2.geojson"
    path = SEED_DIR / filename
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    for feature in payload["features"]:
        if feature["properties"]["name"] == name:
            point = shape(feature["geometry"]).representative_point()
            return round(point.y, 6), round(point.x, 6)
    return None


def reverse(lon: float, lat: float) -> dict[str, Any]:
    """Nearest named place plus administrative location for a map point."""
    if not (
        GHANA_BOUNDS["min_lon"] <= lon <= GHANA_BOUNDS["max_lon"]
        and GHANA_BOUNDS["min_lat"] <= lat <= GHANA_BOUNDS["max_lat"]
    ):
        return {"inside_ghana": False, "place": None}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name,type,lat,lon,region,district,population FROM place_index LIMIT 50000"
        ).fetchall()
    best = None
    for row in rows:
        dx = (row["lon"] - lon) * 111.0
        dy = (row["lat"] - lat) * 111.0
        distance = (dx * dx + dy * dy) ** 0.5
        if best is None or distance < best[0]:
            best = (distance, row)
    admin = locate(lon, lat)
    place = None
    if best is not None and best[0] <= 25:
        row = best[1]
        place = {
            "name": row["name"],
            "type": row["type"],
            "distance_km": round(best[0], 2),
            "lat": row["lat"],
            "lon": row["lon"],
        }
    return {
        "inside_ghana": True,
        "place": place,
        "region": admin["region"],
        "district": admin["district"],
        "match": admin["match"],
    }
