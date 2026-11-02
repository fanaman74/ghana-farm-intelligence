"""Export farm records as GeoJSON, KML, CSV or a printable PDF report."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any


def _farm_feature(farm: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": farm["geometry"],
        "properties": {
            "id": farm["id"],
            "name": farm["name"],
            "area_ha": farm.get("area_ha"),
            "perimeter_m": farm.get("perimeter_m"),
            "region": farm.get("region"),
            "district": farm.get("district"),
            "zone": farm.get("zone"),
            "crop_id": farm.get("crop_id"),
            "crop_variety": farm.get("crop_variety"),
            "planting_date": farm.get("planting_date"),
            "irrigation": farm.get("irrigation"),
            "source": farm.get("source"),
            "is_demo": bool(farm.get("is_demo")),
            "created_at": farm.get("created_at"),
            "updated_at": farm.get("updated_at"),
        },
    }


def farm_geojson(farm: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    features = [_farm_feature(farm)]
    for field in farm.get("fields", []):
        features.append(
            {
                "type": "Feature",
                "geometry": field["geometry"],
                "properties": {
                    "id": field["id"],
                    "name": field["name"],
                    "parent_farm": farm["id"],
                    "area_ha": field.get("area_ha"),
                    "crop_id": field.get("crop_id"),
                    "planting_date": field.get("planting_date"),
                },
            }
        )
    collection = {
        "type": "FeatureCollection",
        "name": f"{farm['name']} — Ghana Farm Intelligence export",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licence_note": "Farm boundaries are private. Contains data © the farm operator.",
        "features": features,
        "records": {
            "soil_tests": farm.get("soil_tests", []),
            "observations": [
                record for record in farm.get("records", []) if record.get("kind") == "observation"
            ],
        },
    }
    if analysis:
        collection["analysis_summary"] = {
            "generated_at": analysis.get("generated_at"),
            "top_actions": [item.get("title") for item in analysis.get("summary", {}).get("top_actions", [])],
        }
    return collection


def _xml_escape(text: Any) -> str:
    value = "" if text is None else str(text)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def farm_kml(farm: dict[str, Any]) -> str:
    from shapely.geometry import shape

    geom = shape(farm["geometry"])
    polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    placemarks = []
    for index, polygon in enumerate(polygons, start=1):
        coordinates = " ".join(f"{lon:.7f},{lat:.7f},0" for lon, lat in polygon.exterior.coords)
        placemarks.append(
            f"""  <Placemark>
    <name>{_xml_escape(farm['name'])} {index if len(polygons) > 1 else ''}</name>
    <description>Area {farm.get('area_ha', 0):.2f} ha. Exported from Ghana Farm Intelligence.</description>
    <styleUrl>#farmStyle</styleUrl>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{coordinates}</coordinates></LinearRing></outerBoundaryIs></Polygon>
  </Placemark>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>{_xml_escape(farm['name'])}</name>
  <Style id="farmStyle">
    <LineStyle><color>ff2f8f4e</color><width>2</width></LineStyle>
    <PolyStyle><color>402f8f4e</color></PolyStyle>
  </Style>
{chr(10).join(placemarks)}
</Document>
</kml>
"""


def farm_csv(farm: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "key", "value"])
    for key in ("id", "name", "area_ha", "perimeter_m", "region", "district", "zone", "crop_id",
                "crop_variety", "planting_date", "irrigation", "source", "is_demo", "created_at", "updated_at"):
        writer.writerow(["farm", key, farm.get(key)])
    for index, field in enumerate(farm.get("fields", []), start=1):
        writer.writerow(["field", f"field_{index}_name", field.get("name")])
        writer.writerow(["field", f"field_{index}_area_ha", field.get("area_ha")])
        writer.writerow(["field", f"field_{index}_crop", field.get("crop_id")])
    for test in farm.get("soil_tests", []):
        writer.writerow(["soil_test", "sampled_on", test.get("sampled_on")])
        for key in ("ph", "soc_gkg", "total_n_gkg", "p_mgkg", "k_mgkg", "cec_cmolkg", "texture_class", "lab_name"):
            writer.writerow(["soil_test", key, test.get(key)])
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _xml_escape(text: str) -> str:
    """Escape ReportLab paragraph markup.

    A farm named "Plot <A>" raised a ValueError and turned PDF export into a 500;
    ReportLab also honours <img>/<a> markup, so unescaped names reach further than
    the paragraph they belong to.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _latin1(text: Any) -> str:
    """reportlab's built-in fonts are Latin-1; normalise typography safely."""
    if text is None:
        return ""
    value = str(text)
    replacements = {
        "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2026": "...", "\u00b0": " deg ", "\u00b7": "-", "\u2022": "-", "\u20b5": "GHS ",
        "\u2264": "<=", "\u2265": ">=", "\u00a0": " ", "\u2080": "0", "\u2081": "1", "\u2082": "2",
        "\u00b3": "3", "\u2070": "0", "\u00b1": "+/-", "\u03bc": "u",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return _xml_escape(value.encode("latin-1", "replace").decode("latin-1"))


def _num(value: Any, digits: int = 2) -> str:
    """Format a number for the report without false precision."""
    if value is None or isinstance(value, bool):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _latin1(value)
    if number != number or number in (float("inf"), float("-inf")):
        return "-"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    return f"{number:.{digits}f}"


def farm_pdf(farm: dict[str, Any], analysis: dict[str, Any] | None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm, title=_latin1(f"Farm report - {farm['name']}"),
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7.5, leading=9.5,
                           textColor=colors.HexColor("#555555"))
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, leading=19,
                        textColor=colors.HexColor("#12321f"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, leading=13,
                        textColor=colors.HexColor("#1d4d33"), spaceBefore=8)
    story: list[Any] = [
        Paragraph("Ghana Farm Intelligence", h1),
        Paragraph(_latin1(f"Farm report — {farm['name']}"), styles["Heading3"]),
        Paragraph(
            _latin1(
                f"Generated {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')} UTC. "
                "Africa/Accra (GMT). All areas are geodesic (WGS84)."
            ),
            small,
        ),
        Spacer(1, 6),
    ]

    summary_rows = [
        ["Area", f"{farm.get('area_ha', 0):,.2f} ha ({farm.get('area_ha', 0) * 2.471:,.2f} acres)"],
        ["Perimeter", f"{farm.get('perimeter_m', 0):,.0f} m"],
        ["Region / district", f"{farm.get('region') or '—'} / {farm.get('district') or '—'}".replace("—", "-")],
        ["Agro-ecological zone", (analysis or {}).get("farm", {}).get("zone", {}).get("name") or farm.get("zone") or "-"],
        ["Crop", (farm.get("crop_id") or "-").replace("_", " ").title()],
        ["Planting date", farm.get("planting_date") or "-"],
        ["Irrigation", farm.get("irrigation") or "-"],
    ]
    table = Table([[Paragraph(_latin1(a), body), Paragraph(_latin1(b), body)] for a, b in summary_rows],
                  colWidths=[52 * mm, 108 * mm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f5f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)

    if not analysis:
        story.append(Spacer(1, 8))
        story.append(Paragraph(_latin1(
            "No analysis is attached to this report. Run 'View data for this area' in the app and export again "
            "for soil, weather, rainfall, vegetation and recommendations."
        ), body))
    else:
        sections = analysis.get("sections", {})
        summary = analysis.get("summary", {})

        story.append(Paragraph("Recommended next actions", h2))
        for index, action in enumerate(summary.get("top_actions", []), start=1):
            story.append(Paragraph(_latin1(f"{index}. {action.get('title')}"), body))
            story.append(Paragraph(_latin1(action.get("action", "")), body))
            story.append(Paragraph(_latin1(f"Why: {action.get('why', '')}"), small))
            story.append(Spacer(1, 3))

        soil = sections.get("soil", {})
        if soil.get("status") in ("ok", "stale"):
            modelled = (soil.get("data") or {}).get("modelled") or {}
            profile = modelled.get("profile_0_30") or {}
            story.append(Paragraph("Soil (SoilGrids estimates, 250 m)", h2))
            rows = [["Property", "Value", "Unit", "Valid coverage"]]
            for key in ("phh2o", "soc", "nitrogen", "clay", "sand", "silt", "cec", "bdod", "cfvo"):
                entry = profile.get(key)
                if not entry:
                    continue
                rows.append([
                    _latin1(entry.get("label", key)),
                    _num(entry.get("value")),
                    _latin1(entry.get("unit", "")),
                    _latin1(f"{(entry.get('valid_fraction') or 0) * 100:.0f}%"),
                ])
            story.append(_table(rows, body, colors))
            story.append(Paragraph(_latin1(
                f"Texture class: {modelled.get('texture_class') or 'unknown'}. "
                "These are model predictions, not laboratory measurements. A local soil test is required before "
                "lime or fertiliser prescriptions."
            ), small))

        weather = sections.get("weather", {})
        if weather.get("status") in ("ok", "stale"):
            data = weather.get("data") or {}
            current = data.get("current") or {}
            grid = data.get("grid") or {}
            story.append(Paragraph("Weather", h2))
            story.append(Paragraph(_latin1(
                f"Current: {current.get('temperature_c')} deg C, {current.get('humidity_pct')}% humidity, "
                f"{current.get('weather')}, wind {current.get('wind_kmh')} km/h. "
                f"Model grid cell at {grid.get('latitude')}, {grid.get('longitude')} "
                f"(~{grid.get('nominal_resolution_km')} km)."
            ), body))
            rows = [["Date", "Rain (mm)", "Prob.", "Tmax", "Tmin", "ET0"]]
            today = datetime.now(timezone.utc).date().isoformat()
            upcoming = [day for day in (data.get("daily") or []) if str(day.get("date")) >= today][:7]
            for day in upcoming:
                rows.append([
                    _latin1(day.get("date")),
                    _latin1(day.get("precipitation_mm")),
                    _latin1(day.get("precipitation_probability")),
                    _latin1(day.get("temperature_max_c")),
                    _latin1(day.get("temperature_min_c")),
                    _latin1(day.get("et0_mm")),
                ])
            story.append(_table(rows, body, colors))

        rainfall = sections.get("rainfall", {})
        if rainfall.get("status") in ("ok", "stale"):
            data = rainfall.get("data") or {}
            story.append(Paragraph("Rainfall (CHIRPS)", h2))
            totals = data.get("windows") or {}
            anomalies = data.get("anomalies") or {}
            rows = [["Period", "Observed", "1991-2020 baseline", "Percent of normal"]]
            for key, label in (("last_7_days", "Last 7 days"), ("last_30_days", "Last 30 days"), ("last_90_days", "Last 90 days")):
                window = totals.get(key) or {}
                anomaly = anomalies.get(key) or {}
                rows.append([
                    _latin1(f"{label} ({window.get('start')} to {window.get('end')})"),
                    _latin1(f"{window.get('total_mm')} mm"),
                    _latin1(f"{anomaly.get('baseline_mm')} mm" if anomaly.get("baseline_mm") is not None else "-"),
                    _latin1(f"{anomaly.get('percent_of_normal')}%" if anomaly.get("percent_of_normal") is not None else "-"),
                ])
            story.append(_table(rows, body, colors))
            story.append(Paragraph(_latin1(
                f"Latest CHIRPS day available: {data.get('latest_available')} "
                f"({data.get('lag_days')} days before today). ~5.5 km estimate, not a rain gauge."
            ), small))

        vegetation = sections.get("vegetation", {})
        if vegetation.get("status") == "ok":
            latest = (vegetation.get("data") or {}).get("latest") or {}
            story.append(Paragraph("Satellite vegetation (Sentinel-2)", h2))
            story.append(Paragraph(_latin1(
                f"Latest usable image: {latest.get('date')} (scene {latest.get('id')}). "
                f"Cloud-free over the field: {(latest.get('cloud_free_fraction') or 0) * 100:.0f}%. "
                f"NDVI mean {(latest.get('ndvi') or {}).get('mean')}, "
                f"NDMI mean {(latest.get('ndmi') or {}).get('mean')}. "
                "Indices show relative condition and are not a pest, disease or nutrient diagnosis."
            ), body))

        crops = sections.get("crops", {})
        ranking = ((crops.get("data") or {}).get("ranking") or [])
        if crops.get("status") == "ok" and ranking:
            story.append(Paragraph("Crop suitability (rule-based screening)", h2))
            rows = [["Crop", "Category", "Score", "Confidence", "Perennial"]]
            for item in ranking:
                rows.append([
                    _latin1(item.get("name")),
                    _latin1(item.get("category_label")),
                    _latin1(item.get("score")),
                    _latin1(item.get("confidence")),
                    _latin1("yes" if item.get("perennial") else "no"),
                ])
            story.append(_table(rows, body, colors))
            story.append(Paragraph(_latin1(
                "Screening only. No yield, price or economic score is produced. See the app for the deciding "
                "factors, missing inputs and cited crop ranges."
            ), small))

        story.append(Paragraph("Data sources and licences", h2))
        rows = [["Dataset", "Provider", "Licence", "Resolution", "Status"]]
        for source in (analysis.get("provenance") or []):
            rows.append([
                _latin1(source.get("dataset", "")),
                _latin1(source.get("provider_id", "")),
                _latin1(source.get("licence", "")),
                _latin1(source.get("resolution") or "-"),
                _latin1(source.get("status") or "-"),
            ])
        story.append(_table(rows, small, colors))

    story.append(Spacer(1, 8))
    story.append(Paragraph(_latin1(
        "Remote sensing and modelled data cannot replace soil testing, field inspection or extension advice. "
        "Ghana Farm Intelligence does not infer land ownership and does not make definitive pest, disease, "
        "fertiliser or yield claims from imagery. Farm boundaries are private and are not published."
    ), small))

    doc.build(story)
    return buffer.getvalue()


def _table(rows: list[list[Any]], style, colors) -> Any:
    from reportlab.lib import colors as colours
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    cell = ParagraphStyle("cell", parent=style, fontSize=7.5, leading=9)
    header = ParagraphStyle("header", parent=cell, textColor=colours.white)
    # Cells arrive already normalised and XML-escaped by _latin1 at their
    # construction site; escaping again here would render "&amp;amp;".
    data = [[Paragraph(value if isinstance(value, str) else _latin1(value), header if index == 0 else cell)
             for value in row] for index, row in enumerate(rows)]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colours.HexColor("#1d4d33")),
        ("GRID", (0, 0), (-1, -1), 0.25, colours.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colours.white, colours.HexColor("#f6f8f5")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table
