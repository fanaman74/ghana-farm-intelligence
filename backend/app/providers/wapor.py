"""FAO WaPOR v3 adapter.

At verification time (2026-10-31) the WaPOR API host returned HTTP 502 on every
documented path, and the Digital Earth Africa mirror held only STAC metadata, not
rasters. No WaPOR value is shown rather than substituting a modelled number. The
adapter keeps probing so the section fills in automatically when the service
returns.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import settings
from ..utils import iso_now
from .base import ProviderError, Section, Source, STATUS_UNAVAILABLE, client
from .registry import provider_map  # noqa: F401

log = logging.getLogger(__name__)

PROVIDER = "wapor"
CATALOG_URL = "https://io.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR/cubes"
PROBE_URL = "https://io.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR"

SOURCE = Source(
    provider_id=PROVIDER,
    dataset="FAO WaPOR v3 — AETI, reference ET, net primary production",
    url="https://www.fao.org/in-action/remote-sensing-for-water-productivity/wapor-data-access/en",
    licence="CC BY-NC-SA 4.0",
    resolution="100 m / 300 m",
    period=None,
    method="WaPOR catalogue API (no key)",
    limitations="Unavailable at verification time. Irrigation need is a planning estimate, not a measured water requirement.",
    access_method="Public HTTPS API (no key)",
    key_required=False,
)


def probe() -> dict[str, Any]:
    """Cheap availability probe. Returns {'reachable': bool, 'message': str}."""
    try:
        response = client().get(PROBE_URL, timeout=20)
        if response.status_code >= 400:
            return {
                "reachable": False,
                "status_code": response.status_code,
                "message": f"FAO WaPOR API returned HTTP {response.status_code}",
            }
        return {"reachable": True, "status_code": response.status_code, "message": "WaPOR catalogue reachable"}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "status_code": None, "message": f"WaPOR probe failed: {exc}"}


def analyse(geom) -> Section:
    status = probe()
    if not status["reachable"]:
        return Section(
            key="water",
            status=STATUS_UNAVAILABLE,
            data={
                "reason": "provider_unavailable",
                "provider_status": status,
                "checked_at": iso_now(),
                "not_shown": ["actual evapotranspiration", "net primary production", "soil moisture (WaPOR)"],
                "substitute_used": "Rainfall (CHIRPS) and reference evapotranspiration (Open-Meteo) only",
            },
            sources=[SOURCE],
            message=(
                "FAO WaPOR did not respond (HTTP 502) at the time of this analysis. No evapotranspiration "
                "or water-productivity value is shown rather than an estimate from another source."
            ),
        )

    # The API is reachable but its coverage workflow for polygon subsets has not
    # been verified against a documented response shape. Do not invent values.
    try:
        payload = client().get(CATALOG_URL, timeout=30).json()
        cubes = _extract_cubes(payload)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        cubes = []
        log.info("WaPOR catalogue could not be parsed: %s", exc)

    if not cubes:
        return Section(
            key="water",
            status=STATUS_UNAVAILABLE,
            data={"reason": "coverage_api_unverified", "cubes_found": 0},
            sources=[SOURCE],
            message=(
                "The WaPOR catalogue responded, but no verified polygon-coverage workflow is implemented in "
                "this release. No WaPOR value is shown."
            ),
        )

    return Section(
        key="water",
        status=STATUS_UNAVAILABLE,
        data={
            "reason": "integration_pending",
            "cubes_found": len(cubes),
            "cube_codes": cubes[:10],
            "note": "Catalogue is reachable; polygon subset extraction is not yet verified.",
        },
        sources=[SOURCE],
        message=(
            "WaPOR is reachable but this release does not yet extract polygon values. "
            "Water advice uses rainfall and reference evapotranspiration only."
        ),
    )


def _extract_cubes(payload: Any) -> list[str]:
    """Best-effort cube code extraction from the WaPOR catalogue shape."""
    output: list[str] = []
    if isinstance(payload, dict):
        for key in ("items", "cubes", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        code = item.get("code") or item.get("id") or item.get("cubeCode")
                        if code:
                            output.append(str(code))
            elif isinstance(value, dict):
                output.extend(_extract_cubes(value))
    return output
