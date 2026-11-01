"""Build the Ghana administrative seed from geoBoundaries gbOpen.

Outputs (all simplified, WGS84):
  backend/data/seed/ghana_adm0.geojson   national outline (for inside-Ghana checks)
  backend/data/seed/ghana_adm1.geojson   16 regions
  backend/data/seed/ghana_adm2.geojson   260 districts
  backend/data/seed/ghana_admin.json     lookup index + provenance

Licences differ by level (ADM0/ADM2 CC BY 4.0, ADM1 CC BY-SA 2.0) and are
recorded per file in the index.

Usage: python scripts/build_ghana_admin.py [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

BACKEND = Path(__file__).resolve().parents[1]
SEED = BACKEND / "data" / "seed"
API = "https://www.geoboundaries.org/api/current/gbOpen/GHA/{level}/"

LEVELS = {
    "ADM0": {"simplify": 0.002, "filename": "ghana_adm0.geojson"},
    "ADM1": {"simplify": 0.0025, "filename": "ghana_adm1.geojson"},
    "ADM2": {"simplify": 0.0012, "filename": "ghana_adm2.geojson"},
}


def simplify_geometry(geom, tolerance: float):
    simplified = geom.simplify(tolerance, preserve_topology=True)
    if simplified.is_empty or simplified.geom_type not in ("Polygon", "MultiPolygon"):
        return geom
    return simplified


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    SEED.mkdir(parents=True, exist_ok=True)

    index: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "levels": {}}
    with httpx.Client(timeout=90, follow_redirects=True,
                      headers={"User-Agent": "GhanaFarmIntelligence/1.0 (admin boundary build)"}) as client:
        for level, spec in LEVELS.items():
            out_path = SEED / spec["filename"]
            print(f"{level}: metadata")
            meta = client.get(API.format(level=level)).json()
            if out_path.exists() and not args.force:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                print(f"  cached {out_path.name} ({len(payload['features'])} features)")
            else:
                url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
                print(f"  downloading {url}")
                raw = client.get(url)
                raw.raise_for_status()
                collection = raw.json()
                features = []
                for feature in collection["features"]:
                    geom = shape(feature["geometry"])
                    geom = simplify_geometry(geom, spec["simplify"])
                    properties = feature.get("properties", {})
                    name = properties.get("shapeName") or properties.get("name") or "Unnamed"
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": geom.geom_type, "coordinates": _round(mapping(geom)["coordinates"], 5)},
                            "properties": {
                                "name": name,
                                "level": level,
                                "shape_id": properties.get("shapeID"),
                            },
                        }
                    )
                payload = {
                    "type": "FeatureCollection",
                    "name": f"Ghana {level} (geoBoundaries gbOpen, simplified)",
                    "licence": meta.get("boundaryLicense"),
                    "source": meta.get("boundarySource") or "geoBoundaries",
                    "generated_at": index["generated_at"],
                    "features": features,
                }
                out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
                print(f"  wrote {out_path.name} ({out_path.stat().st_size / 1e6:.2f} MB, {len(features)} features)")
            index["levels"][level] = {
                "file": spec["filename"],
                "count": len(payload["features"]),
                "licence": meta.get("boundaryLicense"),
                "source": "geoBoundaries gbOpen",
                "url": API.format(level=level),
                "units": [f["properties"]["name"] for f in payload["features"]],
            }

            if level == "ADM0":
                outline = unary_union([shape(f["geometry"]) for f in payload["features"]])
                index["adm0_bounds"] = [round(v, 5) for v in outline.bounds]
                index["adm0_area_km2"] = round(_area_km2(outline))

    (SEED / "ghana_admin.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"\nWrote {SEED / 'ghana_admin.json'}")
    print("regions:", index["levels"]["ADM1"]["count"], "districts:", index["levels"]["ADM2"]["count"])
    return 0


def _round(coords, digits: int):
    if isinstance(coords, (list, tuple)):
        return [_round(value, digits) for value in coords]
    if isinstance(coords, float):
        return round(coords, digits)
    return coords


def _area_km2(geom) -> float:
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    area, _ = geod.geometry_area_perimeter(geom)
    return abs(area) / 1e6


if __name__ == "__main__":
    raise SystemExit(main())
