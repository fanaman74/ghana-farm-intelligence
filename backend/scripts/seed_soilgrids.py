"""Build the bundled SoilGrids VRT metadata seed.

The seed contains only the rewritten VRT XML (gzipped, ~100 KB per layer), not
any raster data. Farm analyses still fetch just the 600×600 source tiles that
cover the polygon, from ISRIC, at read time.

Usage:
    python scripts/seed_soilgrids.py [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.providers.soilgrids_layers import SEED_VRT_DIR, build_seed, vrt_url  # noqa: E402

PROPERTIES = ["phh2o", "soc", "nitrogen", "clay", "sand", "silt", "cec", "bdod", "cfvo"]
DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(SEED_VRT_DIR))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    layers = [(prop, depth, "mean") for prop in PROPERTIES for depth in DEPTHS]
    layers += [("phh2o", depth, stat) for depth in DEPTHS for stat in ("Q0.05", "Q0.95")]

    print(f"Fetching {len(layers)} SoilGrids VRT layers into {args.out}")
    results = build_seed(Path(args.out), layers, workers=args.workers, force=args.force)
    for result in results:
        status = "cached" if result["skipped"] else f"{result['seconds']}s"
        print(f"  {result['layer']}: {result['bytes'] / 1024:.0f} KB ({status})")

    total = sum(result["bytes"] for result in results)
    manifest = {
        "dataset": "ISRIC SoilGrids 2.0 VRT metadata (absolute /vsicurl sources)",
        "provider": "ISRIC — World Soil Information",
        "source": "https://files.isric.org/soilgrids/latest/data/",
        "documentation": "https://docs.isric.org/globaldata/soilgrids/index.html",
        "licence": "CC BY 4.0",
        "attribution": "SoilGrids 2.0 (ISRIC — World Soil Information), CC BY 4.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layers": results,
        "total_bytes": total,
        "grid": {"crs": "EPSG:4326", "approx_resolution_m": 250, "native_tile_px": 600},
        "scale_factors": {prop: (100.0 if prop == "bdod" else 10.0) for prop in PROPERTIES},
        "example_source": vrt_url("phh2o", "0-5cm", "mean"),
        "notes": [
            "Only VRT mosaic indexes are bundled; no SoilGrids raster values are redistributed.",
            "Runtime reads fetch individual 600×600 tiles from ISRIC with GDAL range requests.",
            "WCS GetCoverage was tested and rejected: subsets contained periodic zero artefacts.",
        ],
    }
    (Path(args.out) / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"\nDone: {len(results)} layers, {total / 1024:.0f} KB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
