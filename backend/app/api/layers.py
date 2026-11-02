"""On-demand farm-scale map layers (soil, land cover, terrain)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from .. import analysis as analysis_service
from .. import auth
from ..geo import layer_render
from ..providers import sentinel2

router = APIRouter()


def _load_visible_farm(farm_id: str):
    """Load a farm, reporting another account's boundary as missing."""
    try:
        farm = analysis_service.load_farm(farm_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Farm not found") from exc
    if not auth.can_see_farm(farm.get("owner_id")):
        raise HTTPException(status_code=404, detail="Farm not found")
    return farm


@router.get("/farms/{farm_id}/layers/{layer_id}")
def farm_layer(
    farm_id: str,
    layer_id: str,
    field_id: str | None = Query(default=None),
) -> dict:
    """Render (or reuse) a polygon-masked PNG overlay for the selected farm or field.

    The farm/field geometry is re-read from the database on every request, so a
    layer never belongs to a different boundary.
    """
    try:
        farm = _load_visible_farm(farm_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Farm not found") from exc
    # Accept both URL-friendly (soil-ph) and canonical (soil_ph) ids.
    canonical = layer_id.replace("-", "_")
    try:
        geom, _area, geometry_label = analysis_service.farm_geometry(farm, field_id)
        payload = layer_render.render(canonical, geom)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown layer '{layer_id}'. Available: {', '.join(layer_render.layer_ids())}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - provider failure must be explicit
        raise HTTPException(
            status_code=502,
            detail={
                "code": "layer_render_failed",
                "message": f"This layer could not be rendered: {exc}",
                "layer_id": layer_id,
            },
        ) from exc
    return {
        **payload,
        "farm_id": farm_id,
        "field_id": field_id,
        "geometry_label": geometry_label,
    }


@router.get("/farms/{farm_id}/satellite")
def farm_satellite(
    farm_id: str,
    buffer_m: int = Query(default=1500, ge=800, le=6000),
) -> dict:
    """A real Sentinel-2 true-colour basemap around the farm (10 m, latest clear scene)."""
    try:
        farm = _load_visible_farm(farm_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Farm not found") from exc
    try:
        geom, _area, geometry_label = analysis_service.farm_geometry(farm, None)
        payload = sentinel2.context_image(geom, buffer_m=buffer_m)
    except Exception as exc:  # noqa: BLE001 - report, never fake imagery
        raise HTTPException(
            status_code=502,
            detail={
                "code": "satellite_context_failed",
                "message": f"Sentinel-2 true colour could not be loaded: {exc}",
            },
        ) from exc
    return {**payload, "farm_id": farm_id, "geometry_label": geometry_label}


@router.get("/layers")
def available_layers(response: Response) -> dict:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {
        "layers": layer_render.layer_ids() + ["evapotranspiration"],
        "note": (
            "Layer PNGs are rendered from the same polygon-masked rasters used for the statistics. "
            "Soil and terrain layers are drawn at native 250 m / 30 m resolution; the legend states it."
        ),
    }
