"""Live provider verification (network required).

Run this after a fresh checkout to confirm which providers are reachable from the
deployment network, which is not the same as the deterministic unit tests.

    python scripts/verify_providers.py [--json docs/provider-verification.json]

Each check prints an HTTP status, latency and, where practical, one sampled value
and the licence. A failing provider is reported as UNAVAILABLE, never as a value.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import GHANA_BOUNDS, SEED_DIR  # noqa: E402

FARMS = {
    "south_ejisu": {"lat": 6.6974, "lon": -1.4542, "name": "Southern Ghana (Ejisu, Ashanti)"},
    "north_bolgatanga": {"lat": 10.8185, "lon": -0.9240, "name": "Northern Ghana (Bolgatanga, Upper East)"},
}
S3 = "https://deafrica-input-datasets.s3.af-south-1.amazonaws.com/"
UA = {"User-Agent": "GhanaFarmIntelligence/1.0 (provider verification)"}


def check(name: str, fn) -> dict:
    started = time.perf_counter()
    try:
        detail = fn()
        return {
            "provider": name,
            "status": "ok",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "provider": name,
            "status": "unavailable",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=str(BACKEND.parent / "docs" / "provider-verification.json"))
    args = parser.parse_args()
    results = []
    with httpx.Client(timeout=45, headers=UA, follow_redirects=True) as client:
        def soilgrids():
            from app.db import init_db
            from app.geo.geodesy import ensure_shapely
            from app.providers.soilgrids import _layer_stats

            init_db()
            samples = {}
            for key, farm in FARMS.items():
                delta = 0.003
                geom = ensure_shapely(
                    {
                        "type": "Polygon",
                        "coordinates": [[
                            [farm["lon"] - delta, farm["lat"] - delta],
                            [farm["lon"] + delta, farm["lat"] - delta],
                            [farm["lon"] + delta, farm["lat"] + delta],
                            [farm["lon"] - delta, farm["lat"] + delta],
                            [farm["lon"] - delta, farm["lat"] - delta],
                        ]],
                    }
                )
                stats, _kind = _layer_stats("phh2o", "0-5cm", "mean", geom)
                samples[key] = {
                    "ph_0_5cm": stats["mean"],
                    "valid_fraction": stats["valid_fraction"],
                    "pixels": stats["pixels_in_polygon"],
                    "resolution_m": stats["resolution"]["nominal_m"],
                }
            return {"licence": "CC BY 4.0", "access": "WebDAV/VRT", "samples": samples}

        results.append(check("soilgrids", soilgrids))

        def openmeteo():
            lat = FARMS["south_ejisu"]["lat"]
            lon = FARMS["south_ejisu"]["lon"]
            response = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": "Africa/Accra",
                    "daily": "precipitation_sum,temperature_2m_max,et0_fao_evapotranspiration",
                    "forecast_days": 16,
                    "past_days": 5,
                },
            )
            payload = response.json()
            return {
                "status": response.status_code,
                "grid": [payload.get("latitude"), payload.get("longitude")],
                "days": len((payload.get("daily") or {}).get("time") or []),
                "first": ((payload.get("daily") or {}).get("time") or [None])[0],
                "licence": "CC BY 4.0 (hosted free tier non-commercial)",
            }

        results.append(check("openmeteo_forecast", openmeteo))

        def chirps():
            response = client.get(
                S3,
                params={"list-type": "2", "prefix": "rainfall_chirps_daily/2026/09/", "max-keys": 1000},
            )
            root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(response.text)
            ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
            keys = sorted(
                node.find(ns + "Key").text
                for node in root.findall(ns + "Contents")
                if node.find(ns + "Key") is not None
            )
            latest = keys[-1] if keys else None
            monthly = client.head(S3 + "rainfall_chirps_monthly/chirps-v2.0_2026.09.tif")
            return {
                "status": response.status_code,
                "latest_daily": latest,
                "daily_files_in_month": len(keys),
                "monthly_head": monthly.status_code,
                "licence": "Public domain (UCSB CHC); mirror CC BY 4.0",
            }

        results.append(check("chirps", chirps))

        def sentinel2():
            body = {
                "collections": ["sentinel-2-l2a"],
                "intersects": {"type": "Point", "coordinates": [FARMS["south_ejisu"]["lon"], FARMS["south_ejisu"]["lat"]]},
                "datetime": f"{(date.today() - __import__('datetime').timedelta(days=90)).isoformat()}T00:00:00Z/{date.today().isoformat()}T23:59:59Z",
                "limit": 3,
            }
            response = client.post("https://earth-search.aws.element84.com/v1/search", json=body)
            payload = response.json()
            latest = (payload.get("features") or [{}])[0]
            return {
                "status": response.status_code,
                "items": len(payload.get("features") or []),
                "latest": latest.get("id"),
                "cloud_cover": (latest.get("properties") or {}).get("eo:cloud_cover"),
                "licence": "Copernicus Sentinel data, free and open",
            }

        results.append(check("sentinel2", sentinel2))

        def dem():
            url = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N06_00_W002_00_DEM/Copernicus_DSM_COG_10_N06_00_W002_00_DEM.tif"
            import rasterio

            with rasterio.open("/vsicurl/" + url) as ds:
                window = ds.window(-1.47, 6.69, -1.44, 6.71)
                data = ds.read(1, window=window, masked=True)
                return {
                    "tile": url.rsplit("/", 1)[-1],
                    "resolution_deg": ds.res[0],
                    "mean_elevation_m": round(float(np.nanmean(data)), 1),
                    "licence": "© DLR/Airbus DS, Copernicus DEM",
                }

        results.append(check("copernicus_dem", dem))

        def worldcover():
            import rasterio

            url = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N06W003_Map.tif"
            with rasterio.open("/vsicurl/" + url) as ds:
                window = ds.window(-1.42, 6.49, -1.39, 6.52)  # Lake Bosumtwi
                data = ds.read(1, window=window)
                values, counts = np.unique(data, return_counts=True)
                dominant = int(values[np.argmax(counts)])
                return {
                    "tile": url.rsplit("/", 1)[-1],
                    "dominant_class": dominant,
                    "dominant_share": round(float(counts.max() / counts.sum()), 3),
                    "check": "Lake Bosumtwi should be class 80 (permanent water)",
                    "licence": "CC BY 4.0 (ESA WorldCover)",
                }

        results.append(check("esa_worldcover", worldcover))

        def boundaries():
            response = client.get("https://www.geoboundaries.org/api/current/gbOpen/GHA/ADM2/")
            payload = response.json()
            return {
                "status": response.status_code,
                "units": payload.get("admUnitCount"),
                "licence": payload.get("boundaryLicense"),
            }

        results.append(check("geoboundaries", boundaries))

        def wapor():
            response = client.get("https://io.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR")
            if response.status_code >= 400:
                raise RuntimeError(f"WaPOR catalogue returned HTTP {response.status_code}")
            return {"status": response.status_code, "licence": "CC BY-NC-SA 4.0"}

        results.append(check("wapor", wapor))

        def gaez():
            response = client.get("https://gaez.fao.org/pages/data-access-download")
            return {
                "status": response.status_code,
                "note": "Interactive download workflow; no documented polygon machine API",
                "licence": "CC BY-NC-SA 3.0 IGO",
            }

        results.append(check("gaez", gaez))

    seeds = {
        "places": (SEED_DIR / "ghana_places.json.gz").exists(),
        "roads": (SEED_DIR / "ghana_roads.geojson.gz").exists(),
        "water": (SEED_DIR / "ghana_water.geojson.gz").exists(),
        "adm2": (SEED_DIR / "ghana_adm2.geojson").exists(),
        "chirps_climatology": (SEED_DIR / "chirps" / "ghana_climatology_1991_2020.tif").exists(),
        "soilgrids_vrt": len(list((SEED_DIR / "soilgrids_vrt").glob("*.vrt.gz"))) if (SEED_DIR / "soilgrids_vrt").exists() else 0,
    }
    output = {
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "farms": FARMS,
        "seed_files": seeds,
        "results": results,
        "summary": {
            "ok": sum(1 for item in results if item["status"] == "ok"),
            "unavailable": [item["provider"] for item in results if item["status"] != "ok"],
        },
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(output, indent=1), encoding="utf-8")

    for item in results:
        marker = "OK  " if item["status"] == "ok" else "FAIL"
        detail = item.get("detail") or item.get("error")
        print(f"{marker} {item['provider']:22s} {item['latency_ms']:5d} ms  {str(detail)[:120]}")
    print(f"\nWrote {args.json}")
    return 0 if output["summary"]["ok"] >= 6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
