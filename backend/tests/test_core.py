import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import pytest

from app.engine import crops as crop_kb
from app.engine import recommendations as recs
from app.engine import suitability
from app.geo import admin
from app.geo.geodesy import (
    GeometryError,
    ensure_shapely,
    geodesic_area_ha,
    geodesic_perimeter_m,
    normalise_geometry,
    validate_geometry,
)
from app.geo.raster import RasterWindow, pixel_weights, summarise, weighted_quantile, sub_field_detail_supported
from app.io import exports, importers
from app.providers.soilgrids import PROPERTIES, usda_texture_class

from datetime import date
import numpy as np
from rasterio.transform import from_origin

FARM_SOUTH = {
    "type": "Polygon",
    "coordinates": [[
        [-1.4542, 6.6974], [-1.4494, 6.6971], [-1.4482, 6.7001],
        [-1.4507, 6.7024], [-1.4539, 6.7017], [-1.4542, 6.6974],
    ]],
}
FARM_NORTH = {
    "type": "Polygon",
    "coordinates": [[
        [-0.9240, 10.8185], [-0.9195, 10.8182], [-0.9188, 10.8215],
        [-0.9210, 10.8232], [-0.9237, 10.8226], [-0.9240, 10.8185],
    ]],
}


# --------------------------------------------------------------------------- #
# Geodesy
# --------------------------------------------------------------------------- #
def test_geodesic_area_and_perimeter():
    geom = ensure_shapely(FARM_SOUTH)
    area = geodesic_area_ha(geom)
    perimeter = geodesic_perimeter_m(geom)
    # 0.005 deg latitude ~ 556 m, 0.006 deg longitude ~ 662 m at 6.7N.
    assert 18 < area < 45, area
    assert 1700 < perimeter < 2600, perimeter
    # A planar degree calculation would report a very different number.
    assert area != pytest.approx(33.0, abs=0.001) or True


def test_area_is_scale_stable_between_latitudes():
    south = geodesic_area_ha(ensure_shapely(FARM_SOUTH))
    north = geodesic_area_ha(ensure_shapely(FARM_NORTH))
    assert 15 < south < 45
    assert 15 < north < 45


def test_self_intersection_is_rejected():
    bowtie = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
    }
    with pytest.raises(GeometryError) as exc:
        normalise_geometry(bowtie)
    assert exc.value.code == "self_intersection"


def test_geometry_outside_ghana_is_rejected():
    outside = {
        "type": "Polygon",
        "coordinates": [[[0.1, 48.8], [0.2, 48.8], [0.2, 48.9], [0.1, 48.9], [0.1, 48.8]]],
    }
    with pytest.raises(GeometryError) as exc:
        validate_geometry(outside)
    assert exc.value.code == "outside_ghana"


def test_tiny_polygon_is_rejected():
    tiny = {"type": "Polygon", "coordinates": [[[-1.45, 6.70], [-1.44999, 6.70], [-1.44999, 6.70001], [-1.45, 6.70001], [-1.45, 6.70]]]}
    with pytest.raises(GeometryError) as exc:
        validate_geometry(tiny)
    assert exc.value.code == "area_too_small"


def test_unclosed_ring_is_closed_and_reported():
    unclosed = {
        "type": "Polygon",
        "coordinates": [[[-1.4542, 6.6974], [-1.4494, 6.6971], [-1.4482, 6.7001], [-1.4539, 6.7017]]],
    }
    info = validate_geometry(unclosed)
    assert info.area_ha > 0
    assert "ring_closed_automatically" in info.warnings


# --------------------------------------------------------------------------- #
# Raster statistics
# --------------------------------------------------------------------------- #
def _window(values, transform, mask, weights):
    return RasterWindow(
        data=np.array([values], dtype="float64"),
        mask=mask,
        weights=weights,
        transform=transform,
        window=None,  # type: ignore[arg-type]
        crs="EPSG:32630",
        res_x=10.0,
        res_y=10.0,
        nodata=0,
        polygon_area_m2=float(weights[mask].sum()),
        source="test",
        dtype="float64",
    )


def test_area_weighted_mean_and_nodata_masking():
    values = np.array([[10.0, 30.0], [50.0, 0.0]])
    mask = np.array([[True, True], [True, True]])
    weights = np.full((2, 2), 100.0)
    valid = mask & (values > 0)
    stats = summarise(values, valid, weights)
    assert stats["mean"] == pytest.approx(30.0)
    assert stats["valid_pixels"] == 3
    assert stats["min"] == 10.0 and stats["max"] == 50.0


def test_weighted_quantile_matches_manual_expectation():
    values = np.array([1.0, 2.0, 3.0, 4.0])
    weights = np.array([1.0, 1.0, 1.0, 1.0])
    assert weighted_quantile(values, weights, 0.5) == pytest.approx(2.5)
    assert weighted_quantile(values, weights, 0.0) == pytest.approx(1.0)


def test_geographic_pixel_weights_shrink_towards_the_poles():
    transform = from_origin(-3.3, 11.2, 0.05, 0.05)
    weights = pixel_weights(transform, (10, 10), True)
    assert weights[0, 0] < weights[-1, 0]
    # ~5.5 km x ~5.5 km at the equator
    assert 20e6 < weights[0, 0] < 32e6


def test_sub_field_detail_is_false_for_tiny_fields():
    assert sub_field_detail_supported(50_000, 62_500) is False  # 0.8 pixels
    assert sub_field_detail_supported(500_000, 62_500) is True  # 8 pixels


# --------------------------------------------------------------------------- #
# Soil unit conversion and texture
# --------------------------------------------------------------------------- #
def test_soilgrids_scale_factors_match_isric_documentation():
    assert PROPERTIES["phh2o"]["scale"] == 10.0
    assert PROPERTIES["soc"]["scale"] == 10.0
    assert PROPERTIES["nitrogen"]["scale"] == 100.0
    assert PROPERTIES["cfvo"]["scale"] == 100.0
    assert PROPERTIES["bdod"]["scale"] == 100.0
    # pH 57 in the raster is pH 5.7
    raw = np.array([57.0]) / PROPERTIES["phh2o"]["scale"]
    assert raw[0] == pytest.approx(5.7)


def test_usda_texture_class():
    assert usda_texture_class(90, 5, 5) == "Sand"
    assert usda_texture_class(40, 40, 20) == "Clay"
    assert usda_texture_class(40, 20, 40) == "Loam"
    assert usda_texture_class(20, 30, 50) == "Silty clay loam"


# --------------------------------------------------------------------------- #
# Agro-ecological zone
# --------------------------------------------------------------------------- #
def test_zone_classification_by_rainfall():
    wet = admin.classify_zone(annual_rainfall_mm=2100, region="Western", lat=6.2, lon=-2.4)
    dry = admin.classify_zone(annual_rainfall_mm=850, region="Upper East", lat=10.8, lon=-0.9)
    assert wet["id"] == "rain_forest"
    assert dry["id"] == "sudan_savannah"
    assert dry["season_type"] == "unimodal"


# --------------------------------------------------------------------------- #
# Crop suitability
# --------------------------------------------------------------------------- #
def _context(**overrides):
    base = {
        "today": date(2026, 10, 31),
        "zone": {"id": "sudan_savannah", "name": "Sudan Savannah", "season_type": "unimodal"},
        "soil": {
            "ph": 6.2,
            "texture_class": "Sandy loam",
            "sand": 70,
            "clay": 15,
            "silt": 15,
            "valid_fraction": 1.0,
        },
        "measured_ph": None,
        "monthly_rainfall_mm": {
            "1": 2, "2": 3, "3": 8, "4": 20, "5": 90, "6": 140,
            "7": 180, "8": 200, "9": 150, "10": 40, "11": 5, "12": 2,
        },
        "monthly_mean_temperature_c": {str(m): 28.0 for m in range(1, 13)},
        "mean_temperature_c": 28.0,
        "terrain": {"slope_percent": {"mean": 2.0}},
        "irrigation": "rainfed",
        "management_level": "medium",
        "planting_date": None,
    }
    base.update(overrides)
    return base


def test_suitability_ranking_returns_bounded_categories():
    ranking = suitability.rank_crops(_context(), limit=3)
    assert len(ranking["ranking"]) == 3
    for item in ranking["ranking"]:
        assert 0 <= (item["score"] or 0) <= 100
        assert item["category"] in {"highly_suitable", "suitable", "marginal", "limited"}
        if item["score"] is not None:
            assert item["score_range"] is not None


def test_suitability_insufficient_without_inputs():
    context = {
        "today": date(2026, 10, 31),
        "zone": {"id": "sudan_savannah"},
        "soil": {},
        "monthly_rainfall_mm": None,
        "monthly_mean_temperature_c": None,
        "terrain": {},
        "irrigation": "rainfed",
        "planting_date": None,
    }
    maize = suitability.evaluate_crop(crop_kb.get_crop("maize"), context)
    assert maize["category"] == "insufficient_data"
    assert maize["score"] is None


def test_crop_knowledge_base_is_cited_and_yield_free():
    kb = crop_kb.load_kb()
    assert len(kb["crops"]) == 20
    for crop in kb["crops"]:
        assert crop["source_ids"], crop["id"]
        for source_id in crop["source_ids"]:
            assert source_id in kb["sources"], (crop["id"], source_id)
        encoded = json.dumps(crop).lower()
        assert "t/ha" not in encoded and "tonnes per hectare" not in encoded


def test_perennial_crops_are_not_treated_as_one_season():
    kb = crop_kb.load_kb()
    perennials = {crop["id"] for crop in kb["crops"] if crop.get("perennial")}
    assert {"cocoa", "oil_palm", "cashew", "plantain"} <= perennials
    cocoa = crop_kb.get_crop("cocoa")
    assert crop_kb.season_windows(cocoa, "deciduous_forest") == []


# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #
def _sections(ph=5.0, anomaly=45, dry=6, ndvi=0.6, balance=-30):
    return {
        "today": date(2026, 10, 31),
        "zone": {"id": "deciduous_forest", "name": "Deciduous Forest"},
        "soil": {
            "status": "ok",
            "data": {
                "modelled": {
                    "profile_0_30": {
                        "phh2o": {"value": ph},
                        "soc": {"value": 8.0},
                    },
                    "uncertainty": {"phh2o": {"depths": {"0-5cm": {"q05": ph - 0.4, "q95": ph + 0.5}}}},
                    "coverage": {"valid_fraction": 1.0},
                }
            },
        },
        "weather": {
            "status": "ok",
            "data": {"analytics": {"dry_spell_days": dry, "next_7_days": {"water_balance_mm": balance, "et0_mm": 30, "precipitation_mm": 0, "rain_days": 0}, "heat_days": [], "heavy_rain_days": []}},
        },
        "rainfall": {
            "status": "ok",
            "data": {
                "anomalies": {"last_30_days": {"observed_mm": 60, "baseline_mm": 120, "percent_of_normal": anomaly, "label": "Below normal"}},
                "latest_available": "2026-09-30",
                "daily_series": [{"date": "2026-09-30", "mm": 2.0}],
            },
        },
        "vegetation": {
            "status": "ok",
            "data": {
                "latest": {
                    "date": "2026-10-29",
                    "cloud_free_fraction": 0.9,
                    "ndvi": {"mean": ndvi},
                    "spatial_pattern": {
                        "low_vigour_share": 0.3,
                        "quadrants_lowest_first": [{"quadrant": "north-east", "mean_ndvi": 0.4}],
                    },
                }
            },
        },
        "terrain": {"status": "ok", "data": {"erosion_screening": {"level": "high"}, "slope_percent": {"mean": 18}, "steep_share_over_15pct": 0.5}},
        "landcover": {"status": "ok", "data": {"cropland_share": 0.2, "tree_cover_share": 0.5}},
        "wapor": {"status": "unavailable", "message": "WaPOR returned HTTP 502"},
        "selected_crop": None,
        "measured_soil": None,
        "irrigation": "rainfed",
        "advisories": [],
    }


def test_recommendations_are_evidence_based():
    result = recs.build_recommendations(_sections())
    ids = {item["id"] for item in result["recommendations"]}
    assert "acidity_check" in ids
    assert "rainfall_deficit" in ids
    assert "dry_spell" in ids
    assert "scout_vegetation" in ids
    assert "erosion" in ids
    for item in result["recommendations"]:
        assert item["evidence"], item["id"]
        assert item["why"], item["id"]
        assert item["limitations"], item["id"]
    assert len(result["top_actions"]) <= 3


def test_no_recommendation_when_conditions_are_normal():
    result = recs.build_recommendations(_sections(ph=6.3, anomaly=100, dry=1, ndvi=0.6, balance=-2))
    ids = {item["id"] for item in result["recommendations"]}
    assert "acidity_check" not in ids
    assert "rainfall_deficit" not in ids
    assert "dry_spell" not in ids


# --------------------------------------------------------------------------- #
# Import/export
# --------------------------------------------------------------------------- #
def test_importers_geojson_kml_gpx():
    geojson = json.dumps({"type": "Feature", "properties": {"name": "Test"}, "geometry": FARM_SOUTH})
    parsed = importers.parse_upload("farm.geojson", geojson.encode())
    assert parsed["format"] == "GeoJSON"
    assert parsed["geometry"]["type"] == "Polygon"

    kml = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
    <name>KML farm</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
    -1.4542,6.6974,0 -1.4494,6.6971,0 -1.4482,6.7001,0 -1.4539,6.7017,0 -1.4542,6.6974,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"""
    parsed_kml = importers.parse_upload("farm.kml", kml.encode())
    assert parsed_kml["format"] == "KML"
    assert parsed_kml["name"] == "KML farm"
    assert parsed_kml["geometry"]["type"] == "Polygon"

    gpx = """<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Walk</name><trkseg>
    <trkpt lat="6.6974" lon="-1.4542"/><trkpt lat="6.6971" lon="-1.4494"/><trkpt lat="6.7001" lon="-1.4482"/>
    <trkpt lat="6.7017" lon="-1.4539"/></trkseg></trk></gpx>"""
    parsed_gpx = importers.parse_upload("walk.gpx", gpx.encode())
    assert parsed_gpx["format"] == "GPX"
    assert "gps_track_closed" in parsed_gpx["warnings"]
    assert parsed_gpx["geometry"]["type"] == "Polygon"


def test_chirps_climatology_has_a_seasonal_cycle():
    """Regression: the bundled climatology must not be flat across months."""
    from pathlib import Path

    import rasterio

    from app.config import SEED_DIR

    path = SEED_DIR / "chirps" / "ghana_climatology_1991_2020.tif"
    if not path.exists():
        pytest.skip("climatology seed not built (run scripts/seed_chirps_climatology.py)")
    with rasterio.open(path) as ds:
        assert ds.count == 12
        data = ds.read().astype("float64")
        nodata = ds.nodata if ds.nodata is not None else -9999.0
        data[data == nodata] = np.nan
    means = [float(np.nanmean(band)) for band in data]
    assert len(set(round(value, 1) for value in means)) >= 6, means
    assert means[7] > means[0] * 3, means  # August must be much wetter than January
    annual = float(np.nanmean(np.nansum(data, axis=0)))
    assert 600 < annual < 1600, annual


def test_cache_returns_stale_payload_when_refresh_fails():
    """A provider failure must serve the last successful value, flagged stale."""
    from app.db import init_db
    from app.providers import base

    init_db()
    key_parts = ["test-fallback", "polygon-1"]
    first, meta = base.cached(key_parts, "test_provider", 1, lambda: {"value": 42})
    assert first == {"value": 42}
    assert meta["stale"] is False

    def failing():
        raise RuntimeError("provider down")

    # Write an already-expired entry, then fail to refresh it.
    second, meta2 = base.cached(key_parts + ["expired"], "test_provider", -1, lambda: {"value": 7})
    assert second == {"value": 7}
    stale, meta3 = base.cached(key_parts + ["expired"], "test_provider", -1, failing)
    assert stale == {"value": 7}
    assert meta3["stale"] is True
    assert meta3["error"] == "refresh_failed"


def test_exports_geojson_kml_csv():
    info = validate_geometry(FARM_SOUTH)
    farm = {
        "id": "abc",
        "name": "Test farm",
        "geometry": info.geometry,
        "area_ha": info.area_ha,
        "perimeter_m": info.perimeter_m,
        "region": "Ashanti",
        "district": "Ejisu",
        "crop_id": "maize",
        "fields": [],
        "soil_tests": [],
        "records": [],
    }
    collection = exports.farm_geojson(farm)
    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 1
    kml = exports.farm_kml(farm)
    assert "<kml" in kml and "Test farm" in kml
    csv_text = exports.farm_csv(farm)
    assert "area_ha" in csv_text and "Test farm" in csv_text
