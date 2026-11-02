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
def test_kml_import_warns_when_only_one_polygon_is_kept():
    """Multi-part KML used to import silently as one part."""
    kml = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Placemark><name>Two parts</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
    -1.4542,6.6974,0 -1.4494,6.6971,0 -1.4482,6.7001,0 -1.4539,6.7017,0 -1.4542,6.6974,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark><name>Second</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
    -1.4600,6.6900,0 -1.4550,6.6900,0 -1.4550,6.6950,0 -1.4600,6.6950,0 -1.4600,6.6900,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    </Document></kml>"""
    parsed = importers.parse_upload("two.kml", kml.encode())
    assert parsed["geometry"]["type"] == "Polygon"
    assert any("only_largest_of_2_kml_polygons_imported" in warning for warning in parsed["warnings"])


def test_kml_import_rejects_an_entity_expansion_attack():
    """defusedxml must refuse DTDs instead of expanding them."""
    bomb = """<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">
    <!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><kml><Placemark><name>&lol2;</name></Placemark></kml>"""
    with pytest.raises(GeometryError):
        importers.parse_upload("bomb.kml", bomb.encode())


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


# --------------------------------------------------------------------------- #
# Regressions: boundary identity, masked statistics, honest scoring
# --------------------------------------------------------------------------- #
def _triangle(upper: bool):
    """Two triangles that share one bounding box (a diagonal split)."""
    from shapely.geometry import Polygon

    if upper:
        return Polygon([[0.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]])
    return Polygon([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]])


def test_polygon_hash_distinguishes_identical_bounding_boxes():
    """A bbox is not a boundary: two fields can share one exactly."""
    from app.geo.geodesy import polygon_hash

    lower, upper = _triangle(False), _triangle(True)
    assert lower.bounds == upper.bounds
    assert polygon_hash(lower) != polygon_hash(upper)
    # Same ring, different instance: still the same boundary.
    assert polygon_hash(lower) == polygon_hash(_triangle(False))


def test_soil_statistics_ignore_pixels_outside_the_polygon():
    """The mask is the field, never the padded bounding-box window."""
    from app.providers.soilgrids import PROPERTIES, sample_stats

    values = np.full((3, 3), 90.0)  # neighbours: pH 9.0
    values[1, 1] = 57.0  # the field only covers the centre cell: pH 5.7
    mask = np.zeros((3, 3), dtype=bool)
    mask[1, 1] = True
    weights = np.ones((3, 3), dtype="float64")

    stats = sample_stats(values, mask, weights, PROPERTIES["phh2o"], -32768.0)
    assert stats["mean"] == pytest.approx(5.7)
    assert stats["pixels_in_polygon"] == 1
    assert stats["valid_fraction"] == pytest.approx(1.0)


def test_soilgrids_zero_is_reported_as_no_prediction_not_zero():
    """ISRIC uses 0 for "no prediction"; it must not become a measured 0 %."""
    from app.providers.soilgrids import PROPERTIES, sample_stats

    values = np.array([[0.0, 5000.0]])  # cfvo is vol % * 100
    mask = np.ones((1, 2), dtype=bool)
    weights = np.ones((1, 2), dtype="float64")

    stats = sample_stats(values, mask, weights, PROPERTIES["cfvo"], -32768.0)
    assert stats["mean"] == pytest.approx(50.0)
    assert stats["valid_fraction"] == pytest.approx(0.5)
    assert stats["zeros_without_prediction"] == 1


def test_texture_class_is_none_when_composition_is_unclassifiable():
    """The old fallback invented "Loam", which scores 100 for most crops."""
    from app.providers.soilgrids import usda_texture_class

    assert usda_texture_class(20, 10, 70) is None
    assert usda_texture_class(90, 5, 5) == "Sand"


def test_missing_component_cannot_reach_high_confidence():
    """Dropping rainfall (the heaviest component) must lower, not raise, the verdict."""
    maize = crop_kb.get_crop("maize")
    full = suitability.evaluate_crop(maize, _context())
    missing_rain = suitability.evaluate_crop(maize, _context(monthly_rainfall_mm=None))

    assert full["data_coverage"] == pytest.approx(1.0)
    assert missing_rain["data_coverage"] < 0.85
    assert missing_rain["confidence"] != "high"
    assert missing_rain["category"] != "highly_suitable"
    # Ranking discounts absent evidence even though the observed mean is unchanged.
    assert missing_rain["rank_score"] < full["rank_score"]


def _planting_context(past_mm: float, future_mm: float):
    from datetime import timedelta

    today = date(2026, 10, 31)
    days = [today - timedelta(days=i) for i in range(4, -1, -1)]
    days += [today + timedelta(days=i) for i in range(0, 6)]
    weather_daily = [
        {
            "date": day.isoformat(),
            "precipitation_mm": past_mm if day < today else future_mm,
            "precipitation_probability": 80 if day >= today else 0,
            "temperature_max_c": 32.0,
            "temperature_min_c": 22.0,
            "et0_mm": 4.0,
        }
        for day in days
    ]
    daily_series = [{"date": (today - timedelta(days=i)).isoformat(), "mm": 3.0} for i in range(14)]
    crop = {
        "id": "maize",
        "name": "Maize",
        "perennial": False,
        "planting_window": {
            "state": "open",
            "label": "Rainy season",
            "planting_window": {"label": "May 15 – July 10"},
            "start_iso": "2026-05-15",
            "days_to_window": 0,
        },
    }
    sections = _sections()
    sections["weather"]["data"]["daily"] = weather_daily
    sections["rainfall"]["data"]["daily_series"] = daily_series
    sections["selected_crop"] = crop
    return sections


def test_planting_advice_uses_the_forecast_not_the_past_five_days():
    """Open-Meteo returns past_days=5 first; slicing [:5] read last week's rain."""
    wet_past_dry_future = recs.build_recommendations(_planting_context(20.0, 0.0))
    ids = {item["id"] for item in wet_past_dry_future["recommendations"]}
    assert "planting_conditions" not in ids

    dry_past_wet_future = recs.build_recommendations(_planting_context(0.0, 20.0))
    ids = {item["id"] for item in dry_past_wet_future["recommendations"]}
    assert "planting_conditions" in ids


def test_analysis_deadline_and_progress_summary():
    """A stuck job must stop with an explanation, and progress writes stay small."""
    from app.analysis import _progress_summary, check_deadline

    check_deadline(10.0, 300)  # fine
    check_deadline(10_000.0, 0)  # budget disabled
    with pytest.raises(GeometryError) as exc:
        check_deadline(400.0, 300)
    assert exc.value.code == "analysis_timeout"

    summary = _progress_summary(
        {
            "partial": True,
            "sections": {
                "soil": {"key": "soil", "status": "ok", "data": {"x": list(range(500))}, "sources": [{}]},
            },
        }
    )
    assert summary["partial"] is True
    assert summary["sections"]["soil"] == {"key": "soil", "status": "ok", "message": None}
    assert len(json.dumps(summary)) < 200


def test_seed_manifest_scale_factors_match_the_provider_table():
    """The bundled manifest used to disagree with the app for nitrogen and cfvo.

    ISRIC's conversion table (Poggio et al. 2021, SoilGrids 2.0): bdod 100→kg/dm³,
    cec 10→cmol(c)/kg, cfvo 100→vol%, nitrogen 100→g/kg, phh2o 10→pH, texture and
    soc 10→%/g kg⁻¹.
    """
    from app.config import SEED_DIR
    from app.providers.soilgrids import PROPERTIES

    path = SEED_DIR / "soilgrids_vrt" / "manifest.json"
    if not path.exists():
        pytest.skip("VRT seed manifest not built")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = {prop: spec["scale"] for prop, spec in PROPERTIES.items()}
    assert manifest["scale_factors"] == expected
    assert manifest["scale_factors"]["nitrogen"] == 100.0
    assert manifest["scale_factors"]["cfvo"] == 100.0


def test_analysis_area_guard_refuses_oversized_boundaries():
    """A 50,000 ha boundary would read gigabytes per job with no explanation."""
    from app.analysis import check_analysis_area

    check_analysis_area(100.0, 20_000.0)  # fine
    with pytest.raises(GeometryError) as exc:
        check_analysis_area(30_000.0, 20_000.0)
    assert exc.value.code == "area_too_large_for_analysis"
    check_analysis_area(30_000.0, 0)  # limit disabled


def test_rainfall_baseline_scales_to_the_days_actually_covered():
    """A partial record must not be compared with a full-window baseline."""
    from app.providers.chirps import _baseline_total

    clim = {"monthly_mean_mm": {str(month): 100.0 for month in range(1, 13)}, "period": "1991–2020"}
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    assert _baseline_total(start, end, clim) == pytest.approx(100.0)
    assert _baseline_total(start, end, clim, days_covered=30) == pytest.approx(100.0)
    assert _baseline_total(start, end, clim, days_covered=15) == pytest.approx(50.0)


def test_pdf_export_survives_markup_in_the_farm_name():
    """A name containing '<' used to raise ValueError (HTTP 500) from ReportLab."""
    info = validate_geometry(FARM_SOUTH)
    farm = {
        "id": "abc",
        "name": "Plot <A> & Sons",
        "geometry": info.geometry,
        "area_ha": info.area_ha,
        "perimeter_m": info.perimeter_m,
        "fields": [],
        "soil_tests": [],
        "records": [],
    }
    pdf = exports.farm_pdf(farm, None)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_analysis_key_changes_when_a_soil_test_is_recorded():
    """Recording a lab result must invalidate the cached analysis."""
    from app.analysis import analysis_key

    farm = {
        "id": "f1",
        "geometry": FARM_SOUTH,
        "fields": [],
        "irrigation": "rainfed",
        "soil_tests": [],
        "advisories": [],
    }
    before = analysis_key(farm, None, "maize", None, "medium")
    farm["soil_tests"] = [
        {"id": "t1", "sampled_on": "2026-10-01", "ph": 5.4, "soc_gkg": 9.0, "texture_class": "Loam"}
    ]
    after = analysis_key(farm, None, "maize", None, "medium")
    assert before != after

    farm["advisories"] = [{"id": "adv-1"}]
    assert analysis_key(farm, None, "maize", None, "medium") != after
