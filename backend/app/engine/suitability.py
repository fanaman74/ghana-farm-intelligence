"""Transparent, rule-based crop suitability for a selected polygon.

No GAEZ raster is queried in this release (no machine API for polygon subsets was
found), and no yield is ever estimated. Each score is the weighted mean of
explicit components with published crop ranges, and every component reports the
farm value, the crop requirement and the data source.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from . import crops as crop_kb

WEIGHTS = {
    "water": 0.30,
    "ph": 0.20,
    "texture": 0.15,
    "temperature": 0.15,
    "slope": 0.10,
}

CATEGORY_BANDS = [
    (75.0, "highly_suitable", "Highly suitable"),
    (60.0, "suitable", "Suitable"),
    (45.0, "marginal", "Marginal"),
    (0.0, "limited", "Limited"),
]

TEXTURE_RELATIVES = {
    "sand": ["loamy sand", "sandy loam"],
    "loamy sand": ["sand", "sandy loam"],
    "sandy loam": ["loam", "loamy sand", "sandy clay loam"],
    "loam": ["sandy loam", "silt loam", "clay loam"],
    "silt loam": ["loam", "silt"],
    "silt": ["silt loam"],
    "sandy clay loam": ["clay loam", "sandy loam"],
    "clay loam": ["sandy clay loam", "silty clay loam", "loam"],
    "silty clay loam": ["clay loam", "silt loam"],
    "sandy clay": ["sandy clay loam", "clay"],
    "silty clay": ["silty clay loam", "clay"],
    "clay": ["clay loam", "sandy clay", "silty clay"],
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score_band(
    value: float, low: float, high: float, optimal_low: float | None = None,
    optimal_high: float | None = None, softness: float | None = None,
) -> float:
    """Score a value against a tolerance band, with a higher-scoring optimal band."""
    if optimal_low is None:
        optimal_low = low
    if optimal_high is None:
        optimal_high = high
    if optimal_low <= value <= optimal_high:
        return 100.0
    if low <= value <= high:
        return 78.0
    span = softness or max((high - low) * 0.6, 1.0)
    if value < low:
        return _clamp(78.0 - (low - value) / span * 78.0)
    return _clamp(78.0 - (value - high) / span * 78.0)


def _texture_score(texture: str | None, preferred: list[str]) -> tuple[float | None, str | None]:
    if not texture or not preferred:
        return None, None
    current = texture.lower()
    if current in preferred:
        return 100.0, f"Preferred texture ({texture})"
    relatives = TEXTURE_RELATIVES.get(current, [])
    if any(item in relatives for item in preferred):
        return 70.0, f"Related texture ({texture})"
    return 35.0, f"Texture likely limiting ({texture})"


def _component(
    name: str,
    score: float | None,
    weight: float,
    farm_value: Any,
    requirement: Any,
    source: str,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "score": round(score, 1) if score is not None else None,
        "weight": weight,
        "farm_value": farm_value,
        "requirement": requirement,
        "source": source,
        "note": note,
        "available": score is not None,
    }


def evaluate_crop(crop: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    today: date = context["today"]
    zone = context.get("zone") or {}
    zone_id = zone.get("id")
    soil = context.get("soil") or {}
    terrain = context.get("terrain") or {}
    irrigation = context.get("irrigation")
    perennial = bool(crop.get("perennial"))

    components: list[dict[str, Any]] = []
    missing: list[str] = []

    # --- Water -------------------------------------------------------------
    windows = crop_kb.season_windows(crop, zone_id)
    window = windows[0] if windows else None
    planting_date = context.get("planting_date")
    cycle = int(((crop.get("cycle_days") or {}).get("typical")) or 100)
    monthly_rain = context.get("monthly_rainfall_mm")
    seasonal_rain = None
    if not perennial and window and monthly_rain:
        start = crop_kb.date_for_window(window["planting_window"]["start_month_day"], today.year)
        seasonal_rain = crop_kb.seasonal_rainfall_from_climatology(monthly_rain, start, cycle)
    elif perennial and monthly_rain:
        seasonal_rain = crop_kb.seasonal_rainfall_from_climatology(
            monthly_rain, today, 365
        )  # annual rainfall for perennials

    requirement = (crop.get("water") or {}).get("seasonal_rainfall_mm") or [None, None]
    low, high = (requirement + [None, None])[:2]
    irrigation = (irrigation or "rainfed").lower()
    irrigation_relief = {"rainfed": 0.0, "supplementary": 0.75, "full": 1.0, "irrigated": 1.0}.get(irrigation, 0.0)

    water_score = None
    water_note = None
    if seasonal_rain is not None and low is not None and high is not None:
        baseline = _score_band(float(seasonal_rain), float(low), float(high),
                               float(low), float(high), softness=max(high - low, 150) * 0.8)
        if seasonal_rain < low:
            deficit = (low - seasonal_rain) / max(low, 1)
            relief = min(1.0, deficit * 2) * 40 * irrigation_relief
            water_score = _clamp(baseline + relief)
            water_note = (
                "Seasonal rainfall is below the crop's published range; irrigation would change this result."
                if irrigation_relief == 0
                else "Rainfall is below range, partly offset by stated irrigation."
            )
        else:
            water_score = baseline
            if seasonal_rain > high * 1.35:
                water_note = "Rainfall is well above the crop's range; drainage and disease pressure matter."
        unit = "annual" if perennial else "in-season"
        requirement_text = f"{low:g}–{high:g} mm {unit} rainfall"
        components.append(
            _component(
                "water", water_score, WEIGHTS["water"], f"{seasonal_rain:g} mm",
                requirement_text, "chirps+faocrop", water_note,
            )
        )
    else:
        missing.append("seasonal rainfall climatology")

    # --- pH ----------------------------------------------------------------
    soil_spec = crop.get("soil") or {}
    ph = context.get("measured_ph") or soil.get("ph")
    ph_low, ph_high = (soil_spec.get("ph") or [None, None])[:2]
    ph_opt = (soil_spec.get("ph_optimal") or [ph_low, ph_high])[:2]
    if ph is not None and ph_low is not None:
        score = _score_band(float(ph), float(ph_low), float(ph_high),
                            float(ph_opt[0]), float(ph_opt[1]), softness=1.2)
        note = None
        if float(ph) < float(ph_low):
            note = (
                f"Below the crop's tolerance (pH {ph_low:g}–{ph_high:g}); confirm with a soil test before liming."
            )
        components.append(
            _component(
                "soil pH", score, WEIGHTS["ph"], f"pH {float(ph):g}",
                f"pH {ph_low:g}–{ph_high:g} (optimal {ph_opt[0]:g}–{ph_opt[1]:g})",
                "soilgrids" if not context.get("measured_ph") else "farmer_soil_test",
                note,
            )
        )
    else:
        missing.append("soil pH")

    # --- Texture -----------------------------------------------------------
    texture_score, texture_note = _texture_score(soil.get("texture_class"), soil_spec.get("texture_preferred") or [])
    if texture_score is not None:
        components.append(
            _component(
                "soil texture", texture_score, WEIGHTS["texture"], soil.get("texture_class"),
                ", ".join(soil_spec.get("texture_preferred") or []), "soilgrids", texture_note,
            )
        )
    else:
        missing.append("soil texture")

    # --- Temperature -------------------------------------------------------
    temp_c = crop.get("temperature_c") or {}
    mean_temp = context.get("mean_temperature_c")
    if mean_temp is not None and temp_c.get("optimal"):
        score = _score_band(
            float(mean_temp), float(temp_c.get("mean_min", 10)), float(temp_c.get("mean_max", 40)),
            float(temp_c["optimal"][0]), float(temp_c["optimal"][1]), softness=6.0,
        )
        components.append(
            _component(
                "temperature", score, WEIGHTS["temperature"], f"{float(mean_temp):g} °C mean",
                f"{temp_c.get('mean_min')}–{temp_c.get('mean_max')} °C (optimal {temp_c['optimal'][0]}–{temp_c['optimal'][1]})",
                "openmeteo_archive", temp_c.get("heat_risk_note"),
            )
        )
    else:
        missing.append("temperature normals")

    # --- Slope -------------------------------------------------------------
    slope = (terrain.get("slope_percent") or {}).get("mean")
    slope_max = crop.get("slope_max_percent")
    if slope is not None and slope_max is not None:
        score = 100.0 if slope <= slope_max else _clamp(100.0 - (slope - slope_max) * 4.0)
        components.append(
            _component(
                "slope", score, WEIGHTS["slope"], f"{slope:g} % mean slope",
                f"≤ {slope_max:g} % preferred", "copernicus_dem",
                None if slope <= slope_max else "Steeper than preferred; erosion control is essential.",
            )
        )
    else:
        missing.append("terrain slope")

    available = [component for component in components if component["available"]]
    weight_total = sum(component["weight"] for component in available)
    score = (
        round(sum(component["score"] * component["weight"] for component in available) / weight_total, 1)
        if weight_total > 0
        else None
    )

    soil_valid = soil.get("valid_fraction")
    confidence = "low"
    if score is not None:
        if len(available) >= 4 and (soil_valid is None or soil_valid >= 0.5):
            confidence = "high"
        elif len(available) >= 3:
            confidence = "medium"
    if soil_valid is not None and soil_valid < 0.35:
        confidence = "low"

    category = None
    label = None
    if score is None or len(available) < 3:
        category = "insufficient_data"
        label = "Insufficient data"
        confidence = "insufficient"
    else:
        for threshold, key, text in CATEGORY_BANDS:
            if score >= threshold:
                category, label = key, text
                break
        # A high score from sparse data must not read as certainty.
        category_raw, label_raw = category, label
        if category == "highly_suitable" and confidence != "high":
            category, label = "suitable", "Suitable (data-limited)"
        elif category == "suitable" and confidence == "low":
            category, label = "marginal", "Marginal (data-limited)"
    category_raw = category_raw if score is not None and len(available) >= 3 else None

    # --- Season and calendar ----------------------------------------------
    season = crop_kb.season_status(crop, zone_id, today) if not perennial else None
    current_stage = crop_kb.current_stage(crop, planting_date, today) if planting_date else None
    harvest_window = None
    if window:
        harvest_window = window["harvest_window"]

    constraints = [
        component["note"] for component in available if component.get("note") and component["score"] < 78
    ]
    deciding = sorted(
        [component for component in available], key=lambda item: (item["score"] - 100) * item["weight"]
    )[:3]

    zone_note = (crop.get("agro_ecological_suitability") or {}).get(
        crop_kb.ZONE_TO_KB.get(zone_id or "", "")
    )
    zone_warning = bool(zone_note and any(
        word in zone_note.lower() for word in ("marginal", "not recommended", "poor", "unsuitable")
    ))
    margin = {"high": 5.0, "medium": 10.0, "low": 15.0}.get(confidence, 15.0)
    score_range = (
        [max(0.0, round(score - margin, 1)), min(100.0, round(score + margin, 1))]
        if score is not None
        else None
    )

    return {
        "id": crop["id"],
        "name": crop["name"],
        "scientific_name": crop.get("scientific_name"),
        "local_names": crop.get("local_names", {}),
        "perennial": perennial,
        "category": category,
        "category_label": label,
        "category_uncapped": category_raw,
        "zone_note": zone_note,
        "zone_warning": zone_warning,
        "score_range": score_range,
        "confidence_note": (
            "Category capped because the underlying data is incomplete or low-coverage."
            if category_raw and category != category_raw
            else None
        ),
        "score": score,
        "confidence": confidence,
        "components": components,
        "deciding_factors": [
            {
                "factor": component["name"],
                "farm_value": component["farm_value"],
                "requirement": component["requirement"],
                "score": component["score"],
                "source": component["source"],
                "note": component["note"],
            }
            for component in deciding
        ],
        "constraints": constraints,
        "missing_inputs": missing,
        "planting_window": season,
        "harvest_window": harvest_window,
        "cycle_days": crop.get("cycle_days"),
        "current_stage": current_stage,
        "zone_fit_note": zone_note,
        "common_stresses": crop.get("common_stresses", []),
        "management_note": (crop.get("management_notes") or {}).get(context.get("management_level", "medium")),
        "water_note": (crop.get("water") or {}).get("irrigation_note"),
        "fertility_note": (soil_spec.get("fertility_note")),
        "limitations": crop.get("limitations"),
        "evidence_quality": crop.get("evidence_quality"),
        "sources": crop_kb.sources_for(crop.get("source_ids", [])),
        "seasonal_rainfall_mm": seasonal_rain,
    }


def rank_crops(context: dict[str, Any], *, limit: int = 3) -> dict[str, Any]:
    evaluations = [evaluate_crop(crop, context) for crop in crop_kb.all_crops()]
    scored = [item for item in evaluations if item["category"] not in ("insufficient_data",)]
    scored.sort(key=lambda item: (item["score"] or 0, item["confidence"] == "high"), reverse=True)
    insufficient = [item["name"] for item in evaluations if item["category"] == "insufficient_data"]

    missing = set()
    for item in evaluations:
        missing.update(item["missing_inputs"])

    return {
        "ranking": scored[:limit],
        "all": scored,
        "insufficient": insufficient,
        "missing_inputs": sorted(missing),
        "method": {
            "type": "rule_based_screening",
            "weights": WEIGHTS,
            "category_bands": [
                {"min_score": threshold, "category": key, "label": text}
                for threshold, key, text in CATEGORY_BANDS
            ],
            "note": (
                "Suitability is screening, not a yield forecast. It combines published crop ranges with "
                "SoilGrids, CHIRPS climatology, ERA5 temperature normals and Copernicus terrain. "
                "No GAEZ raster is queried in this release."
            ),
        },
    }
