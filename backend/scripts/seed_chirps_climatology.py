"""Build a Ghana 1991–2020 CHIRPS monthly climatology (12 bands).

Used for rainfall anomalies and seasonal comparisons. Reads only the Ghana window
of each monthly COG (360 rasters) through GDAL range requests, then writes one
small 12-band GeoTIFF with the mean of each calendar month.

Licence: CHIRPS is public domain (UCSB Climate Hazards Center).

Usage:
    python scripts/seed_chirps_climatology.py [--force] [--workers 6]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import GHANA_BOUNDS, SEED_DIR  # noqa: E402

S3_BASE = "https://deafrica-input-datasets.s3.af-south-1.amazonaws.com/"
MONTHLY_KEY = "rainfall_chirps_monthly/chirps-v2.0_{year}.{month:02d}.tif"
START_YEAR, END_YEAR = 1991, 2020
PAD = 0.1
NODATA = -9999.0

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_TIMEOUT": "120",
    "VSI_CACHE": "FALSE",
}


def ghana_window(ds) -> Window:
    west = GHANA_BOUNDS["min_lon"] - PAD
    south = GHANA_BOUNDS["min_lat"] - PAD
    east = GHANA_BOUNDS["max_lon"] + PAD
    north = GHANA_BOUNDS["max_lat"] + PAD
    raw = rasterio.windows.from_bounds(west, south, east, north, transform=ds.transform)
    col_off = max(0, math.floor(raw.col_off))
    row_off = max(0, math.floor(raw.row_off))
    col_end = min(ds.width, math.ceil(raw.col_off + raw.width))
    row_end = min(ds.height, math.ceil(raw.row_off + raw.height))
    return Window(col_off, row_off, max(1, col_end - col_off), max(1, row_end - row_off))


def fetch_year(year: int) -> dict:
    """Return per-month sums and counts: arrays shaped (12, rows, cols)."""
    sums: np.ndarray | None = None
    counts: np.ndarray | None = None
    transform = None
    crs = None
    missing = []
    for month in range(1, 13):
        url = "/vsicurl/" + S3_BASE + MONTHLY_KEY.format(year=year, month=month)
        try:
            with rasterio.Env(**GDAL_ENV):
                with rasterio.open(url) as ds:
                    window = ghana_window(ds)
                    data = ds.read(1, window=window).astype("float32")
                    nodata = ds.nodata if ds.nodata is not None else NODATA
                    data = np.where((data == nodata) | (data < -9000), np.nan, data)
                    if sums is None:
                        sums = np.zeros((12, *data.shape), dtype="float64")
                        counts = np.zeros((12, *data.shape), dtype="int32")
                        transform = ds.window_transform(window)
                        crs = ds.crs
                    if data.shape != sums.shape[1:]:
                        missing.append({"month": month, "error": "shape mismatch"})
                        continue
                    mask = np.isfinite(data)
                    sums[month - 1][mask] += data[mask]
                    counts[month - 1][mask] += 1
        except Exception as exc:  # noqa: BLE001
            missing.append({"month": month, "error": repr(exc)[:200]})
    if sums is None:
        return {"year": year, "error": "no data", "missing": missing}
    return {
        "year": year,
        "sums": sums,
        "counts": counts,
        "transform": transform,
        "crs": crs,
        "missing": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(SEED_DIR / "chirps" / "ghana_climatology_1991_2020.tif"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_path = Path(args.out)
    if out_path.exists() and not args.force:
        print(f"Exists: {out_path} (use --force to rebuild)")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_year, year): year for year in range(START_YEAR, END_YEAR + 1)}
        for future in as_completed(futures):
            year = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f"  {year}: {'ok' if 'sums' in result else 'FAILED'} "
                      f"missing={len(result.get('missing', []))}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {year}: FAILED {exc}")

    usable = [r for r in results if "sums" in r]
    if not usable:
        print("No usable years")
        return 1
    shape = usable[0]["sums"].shape
    transform = usable[0]["transform"]
    crs = usable[0]["crs"]
    total_sum = np.zeros(shape, dtype="float64")
    total_count = np.zeros(shape, dtype="int32")
    for result in usable:
        if result["sums"].shape != shape:
            continue
        total_sum += result["sums"]
        total_count += result["counts"]
    means = np.where(total_count > 0, total_sum / np.maximum(total_count, 1), NODATA)

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 12,
        "width": shape[2],
        "height": shape[1],
        "crs": crs,
        "transform": transform,
        "nodata": NODATA,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        for index in range(12):
            dst.write(means[index].astype("float32"), index + 1)
            dst.set_band_description(index + 1, f"{START_YEAR}-{END_YEAR} mean {index + 1:02d} (mm/month)")
        dst.update_tags(
            SOURCE="CHIRPS v2.0 monthly (UCSB Climate Hazards Center)",
            SOURCE_URL="https://deafrica-input-datasets.s3.af-south-1.amazonaws.com/rainfall_chirps_monthly/",
            LICENCE="CHIRPS is public domain",
            PERIOD=f"{START_YEAR}-{END_YEAR}",
            DERIVED="Per-calendar-month mean rainfall for the Ghana window, 0.05 degrees",
        )

    months_expected = len(range(START_YEAR, END_YEAR + 1)) * 12
    missing_files = [{"year": r["year"], **m} for r in results for m in r.get("missing", [])]
    band_means = {
        str(month + 1): round(float(np.nanmean(np.where(means[month] == NODATA, np.nan, means[month]))), 1)
        for month in range(12)
    }
    manifest = {
        "dataset": "CHIRPS v2.0 1991–2020 monthly climatology (Ghana)",
        "provider": "UCSB Climate Hazards Center, mirrored by Digital Earth Africa",
        "source": S3_BASE + "rainfall_chirps_monthly/",
        "documentation": "https://docs.digitalearthafrica.org/en/latest/data_specs/CHIRPS_specs.html",
        "licence": "Public domain (CHIRPS v2.0)",
        "attribution": "Funk et al. (2015), CHIRPS v2.0, UCSB Climate Hazards Center",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "years": [START_YEAR, END_YEAR],
        "bands": "1–12 = January–December mean rainfall (mm/month)",
        "band_spatial_means_mm": band_means,
        "file": out_path.name,
        "bytes": out_path.stat().st_size,
        "missing_files": missing_files[:20],
        "expected_month_files": months_expected,
        "received_month_files": months_expected - len(missing_files),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "validation": (
            "Per-month means must differ across bands (the seasonal cycle). "
            "Compare a band with the mean of the same calendar month's raw COGs over a few years."
        ),
        "notes": [
            "Values are calendar-month means over the period, not a trend.",
            "Anomalies in the app compare the same calendar days with this baseline.",
        ],
    }
    (out_path.parent / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"\nWrote {out_path} ({manifest['bytes'] / 1e6:.2f} MB), "
          f"{manifest['received_month_files']}/{months_expected} files, "
          f"{manifest['elapsed_seconds']}s")
    print("Band spatial means (mm/month):", band_means)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
