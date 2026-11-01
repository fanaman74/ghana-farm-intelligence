"""Access ISRIC SoilGrids through its documented WebDAV/VRT rasters.

Why not the alternatives
------------------------
* REST API: ISRIC reports the SoilGrids REST service as temporarily paused.
* WCS GetCoverage: tested over Ghana and rejected — subsets contained periodic
  zero artefacts, which would have produced false low values inside fields.
* VRT: the documented bulk path. Each VRT is a mosaic index (3.5 MB of XML);
  reading a farm window fetches only the few 600×600 source tiles it touches.

Each VRT is rewritten to absolute ``/vsicurl/`` source paths and gzipped into the
bundled seed (~100 KB per layer). At runtime it is decompressed to the cache and
opened locally, so no multi-megabyte XML is downloaded per analysis.
"""

from __future__ import annotations

import gzip
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import httpx
import rasterio
from rasterio.enums import Resampling  # noqa: F401  (documented for readers)
from rasterio.vrt import WarpedVRT  # noqa: F401

from ..config import SEED_DIR, settings

log = logging.getLogger(__name__)

VRT_BASE = "https://files.isric.org/soilgrids/latest/data"
WARP_RES = 0.0020833333  # ~250 m
NODATA = -32768.0
SEED_VRT_DIR = SEED_DIR / "soilgrids_vrt"
CACHE_VRT_DIR = settings.cache_dir / "soilgrids_vrt"

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_TIMEOUT": "60",
    "VSI_CACHE": "FALSE",
}

_SOURCE_RE = re.compile(r'<SourceFilename relativeToVRT="1">\./</SourceFilename>')


def layer_name(prop: str, depth: str, stat: str) -> str:
    return f"{prop}_{depth}_{stat}"


def vrt_url(prop: str, depth: str, stat: str) -> str:
    return f"{VRT_BASE}/{prop}/{layer_name(prop, depth, stat)}.vrt"


def rewrite_vrt(xml: str, prop: str) -> str:
    """Point relative mosaic sources at ISRIC's HTTPS COGs for /vsicurl reads."""
    replacement = f'<SourceFilename relativeToVRT="0">/vsicurl/{VRT_BASE}/{prop}/'
    return xml.replace('<SourceFilename relativeToVRT="1">./', replacement)


def local_vrt(prop: str, depth: str, stat: str) -> Path:
    """Return a local, rewritten VRT, restoring it from the seed when possible."""
    name = f"{layer_name(prop, depth, stat)}.vrt"
    CACHE_VRT_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_VRT_DIR / name
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path

    seed_gz = SEED_VRT_DIR / f"{name}.gz"
    if seed_gz.exists():
        xml = gzip.decompress(seed_gz.read_bytes()).decode("utf-8")
        if not xml.lstrip().startswith("<VRTDataset"):
            raise RuntimeError(f"Corrupt VRT seed {seed_gz}")
        cache_path.write_text(xml, encoding="utf-8")
        return cache_path

    # Fallback: fetch from ISRIC once and rewrite locally.
    url = vrt_url(prop, depth, stat)
    log.info("Fetching SoilGrids VRT %s", url)
    response = httpx.get(url, timeout=90, follow_redirects=True,
                         headers={"User-Agent": settings.user_agent})
    response.raise_for_status()
    xml = rewrite_vrt(response.text, prop)
    cache_path.write_text(xml, encoding="utf-8")
    return cache_path


@contextmanager
def open_layer(prop: str, depth: str, stat: str = "mean") -> Iterator[rasterio.DatasetReader]:
    """Yield the SoilGrids layer in its native 250 m equal-area projection.

    Sampling in the native Interrupted Goode Homolosine grid avoids warp
    artefacts and makes pixel area a constant 62,500 m².
    """
    path = local_vrt(prop, depth, stat)
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(str(path)) as dataset:
            yield dataset


def build_seed(output_dir: Path, layers: list[tuple[str, str, str]], *, workers: int = 5,
               force: bool = False, timeout: float = 90.0) -> list[dict]:
    """Download, rewrite and gzip VRT metadata for the bundled seed."""
    import gzip as _gzip
    from concurrent.futures import ThreadPoolExecutor, as_completed

    output_dir.mkdir(parents=True, exist_ok=True)

    def one(layer: tuple[str, str, str]) -> dict:
        prop, depth, stat = layer
        name = f"{layer_name(prop, depth, stat)}.vrt"
        target = output_dir / f"{name}.gz"
        if target.exists() and not force:
            return {"layer": name, "skipped": True, "bytes": target.stat().st_size}
        started = time.perf_counter()
        response = httpx.get(vrt_url(prop, depth, stat), timeout=timeout,
                             follow_redirects=True, headers={"User-Agent": settings.user_agent})
        response.raise_for_status()
        xml = rewrite_vrt(response.text, prop)
        data = _gzip.compress(xml.encode("utf-8"), compresslevel=9, mtime=0)
        target.write_bytes(data)
        return {
            "layer": name,
            "source_bytes": len(response.content),
            "bytes": len(data),
            "seconds": round(time.perf_counter() - started, 1),
            "skipped": False,
        }

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, layer): layer for layer in layers}
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item["layer"])
