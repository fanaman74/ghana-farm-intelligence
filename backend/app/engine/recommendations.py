"""Evidence-based decision rules.

Every recommendation is a rule with an explicit condition, the values that
triggered it, their sources and dates, the uncertainty, and what would change the
advice. Nothing here converts imagery into a pest, disease or yield diagnosis.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

SEVERITY_ORDER = {"warning": 0, "watch": 1, "info": 2, "good": 3}


def _evidence(label: str, value: Any, source: str, detail: str | None = None) -> dict[str, Any]:
    return {"label": label, "value": value, "source": source, "detail": detail}


def _short_day(iso: str) -> str:
    """ISO date -> '31 Oct' for display inside recommendations."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b")
    except (TypeError, ValueError):
        return str(iso)


def _recommendation(
    rec_id: str,
    *,
    title: str,
    action: str,
    why: str,
    severity: str,
    priority: int,
    evidence: list[dict[str, Any]],
    confidence: str,
    limitations: str,
    sources: list[dict[str, Any]] | None = None,
    category: str = "agronomy",
) -> dict[str, Any]:
    return {
        "id": rec_id,
        "title": title,
        "action": action,
        "why": why,
        "severity": severity,
        "priority": priority,
        "evidence": evidence,
        "confidence": confidence,
        "limitations": limitations,
        "sources": sources or [],
        "category": category,
    }


def build_recommendations(context: dict[str, Any]) -> dict[str, Any]:
    today: date = context["today"]
    soil = context.get("soil") or {}
    weather = context.get("weather") or {}
    rainfall = context.get("rainfall") or {}
    vegetation = context.get("vegetation") or {}
    terrain = context.get("terrain") or {}
    landcover = context.get("landcover") or {}
    selected_crop = context.get("selected_crop")
    measured = context.get("measured_soil")
    zone = context.get("zone") or {}
    advisories = context.get("advisories") or []

    recommendations: list[dict[str, Any]] = []
    soil_data = soil.get("data") or {}
    modelled = soil_data.get("modelled") or {}
    profile = modelled.get("profile_0_30") or {}
    ph = (measured or {}).get("ph") or (profile.get("phh2o") or {}).get("value")
    ph_interval = (modelled.get("uncertainty") or {}).get("phh2o", {}).get("depths", {}).get("0-5cm")
    soc = (measured or {}).get("soc_gkg") or (profile.get("soc") or {}).get("value")
    soil_valid = (modelled.get("coverage") or {}).get("valid_fraction")
    soil_status = soil.get("status")
    ph_source = "farmer_soil_test" if (measured or {}).get("ph") else "soilgrids"

    # 1. Soil data absent or too coarse for prescriptions.
    if soil_status in ("unavailable", "error") or (soil_valid is not None and soil_valid < 0.5) or ph is None:
        recommendations.append(
            _recommendation(
                "soil_test",
                title="Get a soil test before fertiliser or lime decisions",
                action=(
                    "Take a representative sample (0–20 cm, 10–15 spots per field) and send it to a "
                    "recognised laboratory. Record the results here so advice switches from estimates to measurements."
                ),
                why=(
                    "Soil values for this field come from a 250 m model estimate"
                    + (f", and only {soil_valid * 100:.0f}% of the field had valid predicted cells" if soil_valid is not None else "")
                    + ". That is not enough for a lime or fertiliser rate."
                ),
                severity="watch",
                priority=2,
                evidence=[
                    _evidence("Soil data status", soil_status or "unknown", "soilgrids"),
                    _evidence("Valid soil coverage", f"{(soil_valid or 0) * 100:.0f}%" if soil_valid is not None else "unknown", "soilgrids"),
                    _evidence("Measured soil test", "none recorded", "farmer_record"),
                ],
                confidence="high",
                limitations="A laboratory result describes the sample, not the whole field; sample depth matters.",
            )
        )

    # 2. Acidity relative to the selected crop (or a general threshold).
    crop_ph = ((selected_crop or {}).get("components") or [])
    crop_ph_component = next((c for c in crop_ph if c["name"] == "soil pH"), None)
    with_irrigation = context.get("irrigation")
    if ph is not None:
        threshold = None
        crop_label = None
        if selected_crop and crop_ph_component:
            requirement = crop_ph_component.get("requirement") or ""
            try:
                threshold = float(requirement.split("pH ")[1].split("–")[0])
            except (IndexError, ValueError):
                threshold = None
            crop_label = selected_crop.get("name")
        general = threshold is None and ph < 5.5
        if (threshold is not None and ph < threshold) or general:
            interval_text = (
                f"90% prediction interval pH {ph_interval['q05']}–{ph_interval['q95']}"
                if ph_interval
                else "no uncertainty interval available"
            )
            recommendations.append(
                _recommendation(
                    "acidity_check",
                    title="Check soil acidity before applying fertiliser",
                    action=(
                        "Confirm pH with a soil test. If it is confirmed low, ask an extension officer about "
                        "liming material and rate for this soil; do not guess a lime rate from an estimate."
                    ),
                    why=(
                        f"Estimated topsoil pH is {ph:g}"
                        + (f", below the {threshold:g} lower limit for {crop_label}" if threshold is not None else " (strongly acidic)")
                        + f". {interval_text}."
                    ),
                    severity="warning",
                    priority=1,
                    evidence=[
                        _evidence("Topsoil pH (0–30 cm)", f"{ph:g}", ph_source),
                        _evidence("Prediction interval", interval_text, "soilgrids"),
                        _evidence(
                            "Crop requirement",
                            crop_ph_component.get("requirement") if crop_ph_component else "pH 5.5–7.0 general",
                            "fao_crop_reference",
                        ),
                    ],
                    confidence="medium" if not measured else "high",
                    limitations="A grid-cell estimate cannot replace a field pH measurement. Lime rates depend on soil texture and target crop.",
                )
            )

    # 3. Organic matter.
    if soc is not None and soc < 12:
        recommendations.append(
            _recommendation(
                "organic_matter",
                title="Build soil organic matter",
                action=(
                    "Keep crop residues on the field where possible, apply well-rotted manure or compost, "
                    "and consider a legume in rotation. Avoid burning residues."
                ),
                why=f"Estimated soil organic carbon is {round(float(soc), 1):g} g/kg, which is low for sustained cropping.",
                severity="info",
                priority=3,
                evidence=[
                    _evidence("Soil organic carbon", f"{round(float(soc), 1):g} g/kg", "farmer_soil_test" if measured else "soilgrids"),
                    _evidence("Typical threshold", "12 g/kg", "fao_crop_reference"),
                ],
                confidence="medium" if not measured else "high",
                limitations="Organic carbon varies within a field and with depth; a single estimate is indicative.",
            )
        )

    # 4. Planting conditions for an annual crop with an open window.
    crop = selected_crop
    if crop and not crop.get("perennial"):
        window = crop.get("planting_window")
        analytics = (weather.get("data") or {}).get("analytics") or {}
        next7 = analytics.get("next_7_days") or {}
        daily = (rainfall.get("data") or {}).get("daily_series") or []
        last14 = [row["mm"] for row in daily[-14:] if row.get("mm") is not None]
        rain14 = round(sum(last14), 1) if last14 else None
        # Open-Meteo is requested with past_days=5, so the daily series begins in
        # the past. Slice from today before summing, or "forecast in the next 5
        # days" is really the previous five days.
        today_iso = today.isoformat()
        forecast_days = [
            row
            for row in ((weather.get("data") or {}).get("daily") or [])
            if str(row.get("date") or "") >= today_iso
        ]
        forecast_known = len(forecast_days) >= 3
        forecast5 = round(sum((row.get("precipitation_mm") or 0) for row in forecast_days[:5]), 1)
        if window and window.get("state") == "open" and rain14 is not None and forecast_known:
            if rain14 >= 20 and forecast5 >= 5:
                recommendations.append(
                    _recommendation(
                        "planting_conditions",
                        title="Planting conditions may improve",
                        action=(
                            "Prepare land now and plant in the next few days if your seedbed and seed are ready. "
                            "Plant on the contour on sloping ground."
                        ),
                        why=(
                            f"{rain14:g} mm fell in the last 14 available days and {forecast5:g} mm is forecast "
                            f"in the next 5 days, inside the {crop['name']} planting window."
                        ),
                        severity="good",
                        priority=1,
                        evidence=[
                            _evidence("Rain, last 14 available days", f"{rain14:g} mm", "chirps"),
                            _evidence("Rain forecast, next 5 days", f"{forecast5:g} mm", "openmeteo_forecast"),
                            _evidence("Planting window", window.get("planting_window", {}).get("label"), "ghana_crop_reference"),
                            _evidence("Historical daily data through", (rainfall.get("data") or {}).get("latest_available"), "chirps"),
                        ],
                        confidence="medium",
                        limitations="Forecast skill beyond ~5 days is limited, and CHIRPS is a 5.5 km estimate. Confirm with your own rain gauge if you have one.",
                    )
                )
            elif rain14 < 10 and forecast5 < 10:
                recommendations.append(
                    _recommendation(
                        "planting_wait",
                        title="Wait for more rain before planting",
                        action="Hold planting until at least 20 mm falls and the forecast shows follow-up rain.",
                        why=(
                            f"Only {rain14:g} mm fell in the last 14 available days and {forecast5:g} mm is "
                            f"forecast in the next 5 days, while the {crop['name']} planting window is open."
                        ),
                        severity="warning",
                        priority=1,
                        evidence=[
                            _evidence("Rain, last 14 available days", f"{rain14:g} mm", "chirps"),
                            _evidence("Rain forecast, next 5 days", f"{forecast5:g} mm", "openmeteo_forecast"),
                            _evidence("Latest CHIRPS day", (rainfall.get("data") or {}).get("latest_available"), "chirps"),
                        ],
                        confidence="medium",
                        limitations="Dry planting can work on some soils; this rule uses rainfall estimates, not soil moisture measured on your field.",
                    )
                )
        elif window and window.get("state") == "upcoming":
            recommendations.append(
                _recommendation(
                    "planting_window_upcoming",
                    title=f"Prepare for the {crop['name']} planting window",
                    action="Complete land preparation, seed and input sourcing before the window opens.",
                    why=(
                        f"The {window.get('label')} planting window opens on {window.get('start_iso')} "
                        f"({window.get('days_to_window')} days from today)."
                    ),
                    severity="info",
                    priority=3,
                    evidence=[
                        _evidence("Window", window.get("planting_window", {}).get("label"), "ghana_crop_reference"),
                        _evidence("Opens", window.get("start_iso"), "ghana_crop_reference"),
                    ],
                    confidence="high",
                    limitations="Season timing varies by locality and by the onset of rains; GMet advisories should refine it.",
                )
            )

    # 5. Dry spell / moisture conservation.
    analytics = (weather.get("data") or {}).get("analytics") or {}
    dry_spell = analytics.get("dry_spell_days")
    balance = (analytics.get("next_7_days") or {}).get("water_balance_mm")
    if (dry_spell is not None and dry_spell >= 4) or (balance is not None and balance < -20):
        recommendations.append(
            _recommendation(
                "dry_spell",
                title="Protect soil moisture over the next week",
                action=(
                    "Mulch around plants where material is available, weed early to reduce competition, "
                    "and irrigate only if you have water and the crop is at a critical stage."
                ),
                why=(
                    f"The forecast shows a {dry_spell}-day dry spell with a water balance of "
                    f"{balance:g} mm (rainfall minus reference evapotranspiration) over the next 7 days."
                    if dry_spell is not None and balance is not None
                    else "The next week's forecast is drier than crop water demand."
                ),
                severity="watch",
                priority=2,
                evidence=[
                    _evidence("Consecutive dry days forecast", dry_spell, "openmeteo_forecast"),
                    _evidence("7-day water balance", f"{balance:g} mm" if balance is not None else None, "openmeteo_forecast"),
                    _evidence("7-day ET₀", f"{(analytics.get('next_7_days') or {}).get('et0_mm'):g} mm", "openmeteo_forecast"),
                ],
                confidence="medium",
                limitations="Forecast rainfall is a grid-cell estimate. Irrigation need is a planning estimate, not a measured water requirement.",
            )
        )

    # 6. Rainfall anomaly.
    anomalies = (rainfall.get("data") or {}).get("anomalies") or {}
    month_anomaly = anomalies.get("last_30_days") or {}
    percent = month_anomaly.get("percent_of_normal")
    latest = (rainfall.get("data") or {}).get("latest_available")
    if percent is not None and percent < 70:
        recommendations.append(
            _recommendation(
                "rainfall_deficit",
                title="Rainfall deficit relative to the 1991–2020 baseline",
                action=(
                    "Plan for reduced water availability: prioritise water for critical growth stages, "
                    "and avoid expanding planted area beyond what you can water."
                ),
                why=(
                    f"The last 30 days of available CHIRPS data (ending {latest}) "
                    f"recorded {month_anomaly.get('observed_mm'):g} mm, {percent:.0f}% of the "
                    f"{month_anomaly.get('baseline_mm'):g} mm long-term average."
                ),
                severity="warning" if percent < 50 else "watch",
                priority=1 if percent < 50 else 2,
                evidence=[
                    _evidence("30-day rainfall", f"{month_anomaly.get('observed_mm'):g} mm", "chirps"),
                    _evidence("1991–2020 baseline", f"{month_anomaly.get('baseline_mm'):g} mm", "chirps_climatology"),
                    _evidence("Percent of normal", f"{percent:.0f}%", "chirps"),
                    _evidence("Data availability", latest, "chirps"),
                ],
                confidence="high",
                limitations="A 30-day average for a 5.5 km cell; local rainfall can differ substantially from the grid estimate.",
            )
        )
    if percent is not None and percent > 140:
        recommendations.append(
            _recommendation(
                "rainfall_excess",
                title="Above-normal rainfall — check drainage and disease risk",
                action="Open drainage channels on low-lying ground and scout for leaf and root diseases after heavy rain.",
                why=f"The last 30 days recorded {percent:.0f}% of the long-term average for this polygon.",
                severity="watch",
                priority=2,
                evidence=[
                    _evidence("30-day rainfall", f"{month_anomaly.get('observed_mm'):g} mm", "chirps"),
                    _evidence("Percent of normal", f"{percent:.0f}%", "chirps"),
                ],
                confidence="high",
                limitations="CRS resolution cannot predict waterlogging in an individual field.",
            )
        )

    # 7. Vegetation anomaly -> scouting request, cause unknown.
    vegetation_data = vegetation.get("data") or {}
    latest_scene = vegetation_data.get("latest") or {}
    pattern = latest_scene.get("spatial_pattern") or {}
    ndvi_mean = (latest_scene.get("ndvi") or {}).get("mean")
    if pattern.get("low_vigour_share") is not None and pattern["low_vigour_share"] >= 0.25 and ndvi_mean is not None:
        quadrant = None
        quadrants = pattern.get("quadrants_lowest_first") or []
        if quadrants:
            quadrant = quadrants[0]["quadrant"]
        recommendations.append(
            _recommendation(
                "scout_vegetation",
                title=f"Inspect the {quadrant or 'least vigorous'} part of the field",
                action=(
                    "Walk that part of the field and look for waterlogging, compaction, pest damage, "
                    "weed pressure or missed fertiliser. Record what you find."
                ),
                why=(
                    f"{pattern['low_vigour_share'] * 100:.0f}% of the field is more than one standard deviation "
                    f"below its own mean NDVI ({ndvi_mean:g}), based on the {latest_scene.get('date')} image. "
                    "The cause is unknown from imagery alone."
                ),
                severity="watch",
                priority=1,
                evidence=[
                    _evidence("NDVI mean", f"{ndvi_mean:g}", "sentinel2"),
                    _evidence("Low-vigour share", f"{pattern['low_vigour_share'] * 100:.0f}%", "sentinel2"),
                    _evidence("Lowest quadrant", quadrant, "sentinel2"),
                    _evidence("Cloud-free fraction", f"{(latest_scene.get('cloud_free_fraction') or 0) * 100:.0f}%", "sentinel2"),
                    _evidence("Image date", latest_scene.get("date"), "sentinel2"),
                ],
                confidence="medium",
                limitations="Vegetation indices reflect canopy condition, not the cause. Pests, disease, nutrients, water and soil differences can look the same.",
            )
        )
    if latest_scene.get("date") is None and vegetation.get("status") == "unavailable":
        recommendations.append(
            _recommendation(
                "no_imagery",
                title="No usable satellite image for this period",
                action="Use field scouting until a cloud-free Sentinel-2 image is available.",
                why="No Sentinel-2 scene with acceptable cloud cover was found for this polygon in the search window.",
                severity="info",
                priority=3,
                evidence=[_evidence("Imagery status", "unavailable", "sentinel2")],
                confidence="high",
                limitations="Absence of imagery is not evidence that the crop is healthy.",
                category="data",
            )
        )

    # 8. Erosion screening.
    erosion = (terrain.get("data") or {}).get("erosion_screening") or {}
    if erosion.get("level") == "high":
        slope = (terrain.get("data") or {}).get("slope_percent") or {}
        recommendations.append(
            _recommendation(
                "erosion",
                title="Reduce erosion risk on this slope",
                action="Plant across the slope, keep soil cover, and use contour bunds or grass strips where the slope is steep.",
                why=f"Mean slope is {slope.get('mean')}% with an erosion screening level of '{erosion.get('level')}'.",
                severity="watch",
                priority=2,
                evidence=[
                    _evidence("Mean slope", f"{slope.get('mean')}%", "copernicus_dem"),
                    _evidence("Share above 15% slope", f"{(terrain.get('data') or {}).get('steep_share_over_15pct', 0) * 100:.0f}%", "copernicus_dem"),
                ],
                confidence="medium",
                limitations="A 30 m DEM is a screening aid, not an erosion or flood model.",
            )
        )

    # 9. Heat risk at a sensitive stage.
    heat_days = analytics.get("heat_days") or []
    stage = (selected_crop or {}).get("current_stage") or {}
    if heat_days and stage.get("name"):
        recommendations.append(
            _recommendation(
                "heat_risk",
                title="Heat stress risk at the current crop stage",
                action="Irrigate early morning or evening if water is available, and avoid cultivation during the hottest hours.",
                why=(
                    f"Maximum temperature is forecast to reach 35 °C or more on {len(heat_days)} day(s) "
                    f"while the crop is at '{stage.get('name')}'."
                ),
                severity="watch",
                priority=2,
                evidence=[
                    _evidence("Hot days", ", ".join(_short_day(day) for day in heat_days[:4]), "openmeteo_forecast"),
                    _evidence("Crop stage", stage.get("name"), "farmer_planting_date"),
                ],
                confidence="low",
                limitations="Air temperature in a grid cell differs from canopy temperature; local shade and soil moisture modify the risk.",
            )
        )

    # 10. Land-cover context (2021 baseline).
    cropland = (landcover.get("data") or {}).get("cropland_share")
    tree = (landcover.get("data") or {}).get("tree_cover_share")
    if cropland is not None and cropland < 0.4 and (tree or 0) > 0.4:
        recommendations.append(
            _recommendation(
                "landcover_context",
                title="Check the field boundary against the 2021 land-cover baseline",
                action="Compare the drawn boundary with the field on the ground, especially if the field is newly cleared.",
                why=f"The 2021 WorldCover baseline shows {tree * 100:.0f}% tree cover and {cropland * 100:.0f}% cropland inside this boundary.",
                severity="info",
                priority=3,
                evidence=[
                    _evidence("Cropland share (2021)", f"{cropland * 100:.0f}%", "esa_worldcover"),
                    _evidence("Tree cover share (2021)", f"{(tree or 0) * 100:.0f}%", "esa_worldcover"),
                ],
                confidence="medium",
                limitations="WorldCover is a 2021 baseline, not a current crop map.",
                category="data",
            )
        )

    # 11. Official advisories.
    for advisory in advisories:
        recommendations.append(
            _recommendation(
                f"advisory_{advisory.get('id')}",
                title=advisory.get("title") or "Official agrometeorological advisory",
                action="Read the full advisory and apply it with local judgement.",
                why=(advisory.get("body") or "")[:600],
                severity="info",
                priority=2,
                evidence=[
                    _evidence("Agency", advisory.get("agency"), "gmet"),
                    _evidence("Published", advisory.get("published_at"), "gmet"),
                    _evidence("Valid", f"{advisory.get('valid_from') or ''} – {advisory.get('valid_to') or ''}", "gmet"),
                ],
                confidence="high",
                limitations="Official advice is regional; local conditions may differ.",
                sources=[{"title": "GMet advisory", "url": advisory.get("source_url"), "publisher": advisory.get("agency")}],
                category="official_advisory",
            )
        )

    # 12. Water-balance transparency when WaPOR is unavailable.
    wapor = context.get("wapor") or {}
    if wapor.get("status") == "unavailable":
        recommendations.append(
            _recommendation(
                "wapor_unavailable",
                title="Evapotranspiration data is currently unavailable",
                action="Water balance below uses rainfall and reference evapotranspiration only; treat it as a planning guide.",
                why=wapor.get("message") or "The WaPOR service did not respond.",
                severity="info",
                priority=3,
                evidence=[_evidence("WaPOR status", "unavailable", "wapor")],
                confidence="high",
                limitations="No WaPOR values are shown rather than substituting a modelled number.",
                category="data",
            )
        )

    recommendations.sort(key=lambda item: (item["priority"], SEVERITY_ORDER.get(item["severity"], 9)))
    # Data-status notices are kept in the full list but are not "next actions" for the field.
    actionable = [item for item in recommendations if item["category"] != "data"]
    top = actionable[:3]
    return {
        "recommendations": recommendations,
        "top_actions": top,
        "counts": {
            "warning": sum(1 for item in recommendations if item["severity"] == "warning"),
            "watch": sum(1 for item in recommendations if item["severity"] == "watch"),
            "info": sum(1 for item in recommendations if item["severity"] == "info"),
            "good": sum(1 for item in recommendations if item["severity"] == "good"),
        },
    }


def calendar_tasks(
    crop: dict[str, Any] | None, planting_date: date | None, today: date,
    zone: dict[str, Any] | None, forecast_dry_days: int | None = None,
) -> dict[str, Any]:
    """Milestones and dated tasks for the selected crop."""
    if not crop:
        return {"milestones": [], "tasks": [], "alerts": []}

    stages = crop.get("growth_stages") or []
    milestones = []
    tasks = []
    for stage in stages:
        start = (planting_date + timedelta(days=int(stage.get("start_day") or 0))) if planting_date else None
        end = (planting_date + timedelta(days=int(stage.get("end_day") or 0))) if planting_date else None
        milestones.append(
            {
                "name": stage.get("name"),
                "start_day": stage.get("start_day"),
                "end_day": stage.get("end_day"),
                "start_date": start.isoformat() if start else None,
                "end_date": end.isoformat() if end else None,
                "notes": stage.get("notes"),
                "status": _milestone_status(start, end, today),
            }
        )
        if start:
            tasks.append(
                {
                    "title": stage.get("name"),
                    "due_date": start.isoformat(),
                    "window_end": end.isoformat() if end else None,
                    "notes": stage.get("notes"),
                    "kind": "crop_stage",
                    "days_until": (start - today).days,
                }
            )

    if not planting_date:
        window = None
        if crop.get("seasons"):
            from . import crops as crop_kb

            window = crop_kb.season_status(crop, (zone or {}).get("id"), today)
        if window:
            tasks.insert(
                0,
                {
                    "title": f"Plant {crop.get('name')}",
                    "due_date": window.get("start_iso"),
                    "window_end": window.get("end_iso"),
                    "notes": f"{window.get('label')} planting window",
                    "kind": "planting",
                    "days_until": window.get("days_to_window"),
                },
            )

    alerts = [
        task for task in tasks
        if task.get("days_until") is not None and 0 <= task["days_until"] <= 21
    ]
    if forecast_dry_days and forecast_dry_days >= 5:
        alerts.insert(0, {
            "title": "Dry spell: protect moisture",
            "due_date": (today + timedelta(days=1)).isoformat(),
            "notes": f"{forecast_dry_days} consecutive dry days are forecast.",
            "kind": "weather",
            "days_until": 1,
        })

    return {
        "milestones": milestones,
        "tasks": sorted(tasks, key=lambda item: item.get("due_date") or "9999"),
        "alerts": alerts,
    }


def _milestone_status(start: date | None, end: date | None, today: date) -> str:
    if start is None or end is None:
        return "unscheduled"
    if today < start:
        return "upcoming"
    if today > end:
        return "past"
    return "current"
