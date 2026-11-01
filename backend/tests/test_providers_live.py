"""Live provider integration tests (network required).

Skipped unless ``GFI_LIVE_TESTS=1``. These exercise real provider data for one
southern and one northern Ghana polygon, and check that a provider failure is
reported as a failure rather than silently producing a value.

    GFI_LIVE_TESTS=1 python -m pytest backend/tests/test_providers_live.py -q
"""

from __future__ import annotations

import os

import pytest

from app.db import init_db
from app.geo.geodesy import ensure_shapely
from app.providers import chirps, openmeteo, sentinel2, soilgrids, terrain

pytestmark = pytest.mark.skipif(
    os.environ.get("GFI_LIVE_TESTS") != "1",
    reason="Set GFI_LIVE_TESTS=1 to run network provider tests",
)

SOUTH = ensure_shapely(
    {
        "type": "Polygon",
        "coordinates": [[
            [-1.4702, 6.6851], [-1.4652, 6.6848], [-1.4639, 6.6877],
            [-1.4664, 6.6901], [-1.4700, 6.6894], [-1.4702, 6.6851],
        ]],
    }
)
NORTH = ensure_shapely(
    {
        "type": "Polygon",
        "coordinates": [[
            [-0.9240, 10.8185], [-0.9195, 10.8182], [-0.9188, 10.8215],
            [-0.9210, 10.8232], [-0.9237, 10.8226], [-0.9240, 10.8185],
        ]],
    }
)


@pytest.fixture(autouse=True)
def _database():
    init_db()


@pytest.mark.parametrize("geom,label", [(SOUTH, "south"), (NORTH, "north")])
def test_soilgrids_polygon(geom, label):
    section = soilgrids.analyse(geom, include_uncertainty=False)
    assert section.status in ("ok", "stale")
    profile = section.data["modelled"]["profile_0_30"]
    ph = profile["phh2o"]["value"]
    assert 3.0 < ph < 10.0, (label, ph)
    assert section.data["modelled"]["texture_class"]
    assert section.sources[0].resolution == "250 m (native equal-area grid)"


@pytest.mark.parametrize("geom,label", [(SOUTH, "south"), (NORTH, "north")])
def test_weather_polygon(geom, label):
    section = openmeteo.analyse(geom)
    assert section.status == "ok"
    daily = section.data["daily"]
    assert len(daily) >= 16
    assert daily[0]["date"] <= daily[-1]["date"]
    grid = section.data["grid"]
    assert grid["latitude"] is not None and grid["longitude"] is not None
    # The model grid cell is not claimed to be the farm itself.
    assert section.data["grid_distance_km"] is not None


@pytest.mark.parametrize("geom,label", [(SOUTH, "south"), (NORTH, "north")])
def test_chirps_totals_are_dated(geom, label):
    section = chirps.analyse(geom)
    data = section.data
    assert data["latest_available"]
    assert data["lag_days"] >= 0
    for key in ("last_7_days", "last_30_days", "last_90_days"):
        window = data["windows"][key]
        assert window["end"] == data["latest_available"]
        assert window["total_mm"] >= 0
    anomaly = data["anomalies"]["last_30_days"]
    assert anomaly["label"]


@pytest.mark.parametrize("geom,label", [(SOUTH, "south"), (NORTH, "north")])
def test_dem_and_sentinel2(geom, label):
    dem = terrain.analyse(geom)
    assert dem.status == "ok"
    assert dem.data["elevation_m"]["mean"] is not None
    assert dem.data["slope_percent"]["mean"] is not None

    vegetation = sentinel2.analyse(geom, months=12, max_candidates=4)
    assert vegetation.status in ("ok", "unavailable")
    if vegetation.status == "ok":
        latest = vegetation.data["latest"]
        assert latest["date"]
        assert 0 <= latest["cloud_free_fraction"] <= 1
        if latest["ndvi"]["status"] == "ok":
            assert -1 <= latest["ndvi"]["mean"] <= 1
        assert latest["chips"]["ndvi"].startswith("/api/chips/")


def test_chirps_climatology_matches_raw_monthly_data():
    """Validate the bundled 1991–2020 September mean against five raw monthly COGs."""
    climatology = chirps.climatology(SOUTH)
    assert climatology is not None
    seed_september = climatology["monthly_mean_mm"]["9"]
    raw = [chirps.monthly_value(year, 9, SOUTH)["mm"] for year in range(2015, 2020)]
    raw_mean = sum(raw) / len(raw)
    assert seed_september is not None and raw_mean > 0
    difference = abs(seed_september - raw_mean) / raw_mean
    # A 30-year mean and a 5-year sample differ, but not wildly.
    assert difference < 0.35, (seed_september, raw_mean, raw)


def test_sentinel2_satellite_basemap_is_real():
    result = sentinel2.context_image(SOUTH, buffer_m=1500)
    assert result["available"] is True, result
    assert len(result["corners"]) == 4
    assert result["scene_date"] and len(result["scene_date"]) == 10
    assert result["resolution_m"] == 10
    assert result["url"].endswith(".jpg")


def test_wapor_failure_is_reported_not_faked():
    from app.providers import wapor

    section = wapor.analyse(SOUTH)
    # The API returned HTTP 502 at verification time. Whether it is reachable later,
    # this release must never invent evapotranspiration values.
    assert section.status == "unavailable"
    assert "advection" not in str(section.data).lower()
    if section.data.get("reason") == "provider_unavailable":
        assert section.data["not_shown"]
        assert "Rainfall" in section.data["substitute_used"]
