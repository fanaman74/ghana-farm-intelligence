"""Farm CRUD, geometry tools, records, analysis jobs and exports."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from .. import analysis as analysis_service
from ..config import settings
from ..db import get_conn, job_get, job_latest_for_farm
from ..geo import admin
from ..geo import places as places_service
from ..geo.geodesy import GeometryError, ensure_shapely, overlap_report, split_polygon, validate_geometry
from ..io import exports, importers
from ..schemas import (
    AnalysisRequest,
    FarmCreate,
    FarmUpdate,
    FieldCreate,
    FieldUpdate,
    GeometrySplitRequest,
    GeometryValidateRequest,
    RecordCreate,
    SoilTestCreate,
)
from ..utils import iso_now

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _farm_row(farm_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM farms WHERE id=?", (farm_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    return dict(row)


def _other_geometries(exclude_farm_id: str | None = None) -> list[tuple[str, str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,name,geometry FROM farms WHERE id != ?", (exclude_farm_id or "",)
        ).fetchall()
        fields = conn.execute(
            "SELECT id,name,geometry FROM farm_fields WHERE farm_id != ?", (exclude_farm_id or "",)
        ).fetchall()
    output = []
    for row in list(rows) + list(fields):
        try:
            output.append((row["id"], row["name"] or "Field", json.loads(row["geometry"])))
        except json.JSONDecodeError:
            continue
    return output


def _serialise_farm(row: dict[str, Any], *, include_children: bool = True) -> dict[str, Any]:
    payload = dict(row)
    payload["geometry"] = json.loads(row["geometry"])
    payload["is_demo"] = bool(row.get("is_demo"))
    if include_children:
        with get_conn() as conn:
            fields = conn.execute(
                "SELECT * FROM farm_fields WHERE farm_id=? ORDER BY created_at", (row["id"],)
            ).fetchall()
            tests = conn.execute(
                "SELECT * FROM soil_tests WHERE farm_id=? ORDER BY sampled_on DESC", (row["id"],)
            ).fetchall()
            records = conn.execute(
                "SELECT * FROM farm_records WHERE farm_id=? ORDER BY recorded_at DESC LIMIT 100",
                (row["id"],),
            ).fetchall()
        payload["fields"] = [{**dict(field), "geometry": json.loads(field["geometry"])} for field in fields]
        payload["soil_tests"] = [dict(test) for test in tests]
        payload["records"] = [
            {**dict(record), "payload": json.loads(record["payload"]) if record["payload"] else None}
            for record in records
        ]
    return payload


@router.get("/farms")
def list_farms() -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM farms ORDER BY updated_at DESC").fetchall()
        field_counts = {
            row["farm_id"]: row["count"]
            for row in conn.execute("SELECT farm_id, COUNT(*) AS count FROM farm_fields GROUP BY farm_id").fetchall()
        }
    farms = []
    for row in rows:
        farms.append(
            {
                "id": row["id"],
                "name": row["name"],
                "area_ha": row["area_ha"],
                "perimeter_m": row["perimeter_m"],
                "region": row["region"],
                "district": row["district"],
                "zone": row["zone"],
                "crop_id": row["crop_id"],
                "planting_date": row["planting_date"],
                "irrigation": row["irrigation"],
                "source": row["source"],
                "is_demo": bool(row["is_demo"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "field_count": field_counts.get(row["id"], 0),
            }
        )
    return {"farms": farms, "count": len(farms)}


@router.post("/farms", status_code=201)
def create_farm(body: FarmCreate) -> dict[str, Any]:
    try:
        info = validate_geometry(body.geometry, repair=body.repair)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=exc.as_dict()) from exc

    overlaps = overlap_report(ensure_shapely(info.geometry), _other_geometries())
    location = admin.locate(info.centroid_lon, info.centroid_lat)
    farm_id = uuid.uuid4().hex
    now = _now()
    zone = admin.classify_zone(
        annual_rainfall_mm=None, region=body.region or location.get("region"),
        lat=info.centroid_lat, lon=info.centroid_lon,
    )
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO farms (id,name,region,district,zone,crop_id,crop_variety,planting_date,irrigation,
            notes,geometry,area_ha,perimeter_m,centroid_lat,centroid_lon,source,is_demo,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                farm_id,
                body.name,
                body.region or location.get("region"),
                body.district or location.get("district"),
                zone["name"],
                body.crop_id,
                body.crop_variety,
                body.planting_date,
                body.irrigation,
                body.notes,
                json.dumps(info.geometry),
                info.area_ha,
                info.perimeter_m,
                info.centroid_lat,
                info.centroid_lon,
                body.source,
                1 if body.is_demo else 0,
                now,
                now,
            ),
        )
    farm = _serialise_farm(_farm_row(farm_id))
    return {
        "farm": farm,
        "validation": info.as_dict(),
        "overlaps": overlaps,
        "location": location,
        "warnings": info.warnings + ([f"Overlaps {len(overlaps)} existing boundary(ies)."] if overlaps else []),
    }


@router.get("/farms/{farm_id}")
def get_farm(farm_id: str) -> dict[str, Any]:
    return {"farm": _serialise_farm(_farm_row(farm_id))}


@router.patch("/farms/{farm_id}")
def update_farm(farm_id: str, body: FarmUpdate) -> dict[str, Any]:
    row = _farm_row(farm_id)
    updates: dict[str, Any] = {}
    warnings: list[str] = []
    for field in ("name", "region", "district", "crop_id", "crop_variety", "planting_date", "irrigation", "notes"):
        value = getattr(body, field)
        if value is not None:
            updates[field] = value
    if body.geometry is not None:
        try:
            info = validate_geometry(body.geometry, repair=body.repair)
        except GeometryError as exc:
            raise HTTPException(status_code=422, detail=exc.as_dict()) from exc
        updates.update(
            {
                "geometry": json.dumps(info.geometry),
                "area_ha": info.area_ha,
                "perimeter_m": info.perimeter_m,
                "centroid_lat": info.centroid_lat,
                "centroid_lon": info.centroid_lon,
            }
        )
        warnings.extend(info.warnings)
        location = admin.locate(info.centroid_lon, info.centroid_lat)
        if body.region is None and location.get("region"):
            updates["region"] = location["region"]
        if body.district is None and location.get("district"):
            updates["district"] = location["district"]
    if not updates:
        return {"farm": _serialise_farm(row), "warnings": ["No changes supplied."]}
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in updates)
    with get_conn() as conn:
        conn.execute(f"UPDATE farms SET {assignments} WHERE id=?", (*updates.values(), farm_id))
    return {"farm": _serialise_farm(_farm_row(farm_id)), "warnings": warnings}


@router.delete("/farms/{farm_id}", status_code=204)
def delete_farm(farm_id: str) -> Response:
    _farm_row(farm_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM farms WHERE id=?", (farm_id,))
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Fields
# --------------------------------------------------------------------------- #
@router.post("/farms/{farm_id}/fields", status_code=201)
def create_field(farm_id: str, body: FieldCreate) -> dict[str, Any]:
    farm = _farm_row(farm_id)
    try:
        info = validate_geometry(body.geometry, repair=body.repair)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=exc.as_dict()) from exc
    farm_geom = ensure_shapely(json.loads(farm["geometry"]))
    field_geom = ensure_shapely(info.geometry)
    warnings = list(info.warnings)
    if not farm_geom.buffer(0.00005).contains(field_geom):
        warnings.append("This field extends outside the farm boundary.")
    field_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO farm_fields (id,farm_id,name,crop_id,crop_variety,planting_date,irrigation,
            geometry,area_ha,perimeter_m,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                field_id, farm_id, body.name, body.crop_id, body.crop_variety, body.planting_date,
                body.irrigation, json.dumps(info.geometry), info.area_ha, info.perimeter_m, now, now,
            ),
        )
        conn.execute("UPDATE farms SET updated_at=? WHERE id=?", (now, farm_id))
    return {"field": {**_field_row(field_id)}, "validation": info.as_dict(), "warnings": warnings}


def _field_row(field_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM farm_fields WHERE id=?", (field_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Field not found")
    payload = dict(row)
    payload["geometry"] = json.loads(row["geometry"])
    return payload


@router.patch("/farms/{farm_id}/fields/{field_id}")
def update_field(farm_id: str, field_id: str, body: FieldUpdate) -> dict[str, Any]:
    _farm_row(farm_id)
    updates: dict[str, Any] = {}
    for field in ("name", "crop_id", "crop_variety", "planting_date", "irrigation"):
        value = getattr(body, field)
        if value is not None:
            updates[field] = value
    if body.geometry is not None:
        info = validate_geometry(body.geometry)
        updates.update(
            {
                "geometry": json.dumps(info.geometry),
                "area_ha": info.area_ha,
                "perimeter_m": info.perimeter_m,
            }
        )
    if not updates:
        return {"field": _field_row(field_id)}
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in updates)
    with get_conn() as conn:
        conn.execute(f"UPDATE farm_fields SET {assignments} WHERE id=?", (*updates.values(), field_id))
    return {"field": _field_row(field_id)}


@router.delete("/farms/{farm_id}/fields/{field_id}", status_code=204)
def delete_field(farm_id: str, field_id: str) -> Response:
    with get_conn() as conn:
        conn.execute("DELETE FROM farm_fields WHERE id=? AND farm_id=?", (field_id, farm_id))
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Geometry tools
# --------------------------------------------------------------------------- #
@router.post("/geometry/validate")
def validate_geometry_endpoint(body: GeometryValidateRequest) -> dict[str, Any]:
    try:
        info = validate_geometry(body.geometry, repair=body.repair)
    except GeometryError as exc:
        return JSONResponse(status_code=422, content={"valid": False, "error": exc.as_dict()})
    overlaps = overlap_report(ensure_shapely(info.geometry), _other_geometries()) if body.check_overlaps else []
    location = admin.locate(info.centroid_lon, info.centroid_lat)
    return {
        "valid": True,
        "geometry": info.as_dict(),
        "overlaps": overlaps,
        "location": location,
        "warnings": info.warnings,
    }


@router.post("/geometry/split")
def split_geometry_endpoint(body: GeometrySplitRequest) -> dict[str, Any]:
    from shapely.geometry import shape

    try:
        geom = ensure_shapely(body.geometry)
        line_payload = body.line
        if line_payload.get("type") == "Feature":
            line_payload = line_payload["geometry"]
        line = shape(line_payload)
        if line.geom_type not in ("LineString", "MultiLineString"):
            raise GeometryError("invalid_line", "The split tool needs a line that crosses the boundary.")
        parts = split_polygon(geom, line)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=exc.as_dict()) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail={"code": "split_failed", "message": str(exc)}) from exc
    return {
        "parts": [
            {
                "geometry": validate_geometry(part).geometry,
                "area_ha": validate_geometry(part).area_ha,
            }
            for part in parts
        ]
    }


@router.post("/farms/import", status_code=201)
async def import_farm(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    crop_id: str | None = Form(default=None),
) -> dict[str, Any]:
    content = await file.read()
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File is larger than 12 MB")
    try:
        parsed = importers.parse_upload(file.filename or "boundary.geojson", content)
        info = validate_geometry(parsed["geometry"])
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=exc.as_dict()) from exc

    overlaps = overlap_report(ensure_shapely(info.geometry), _other_geometries())
    location = admin.locate(info.centroid_lon, info.centroid_lat)
    farm_id = uuid.uuid4().hex
    now = _now()
    farm_name = (name or parsed.get("name") or file.filename or "Imported farm").strip()[:120]
    zone = admin.classify_zone(
        annual_rainfall_mm=None, region=location.get("region"), lat=info.centroid_lat, lon=info.centroid_lon
    )
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO farms (id,name,region,district,zone,crop_id,crop_variety,planting_date,irrigation,
            notes,geometry,area_ha,perimeter_m,centroid_lat,centroid_lon,source,is_demo,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                farm_id, farm_name, location.get("region"), location.get("district"), zone["name"],
                crop_id, None, None, "rainfed", f"Imported from {parsed['format']}",
                json.dumps(info.geometry), info.area_ha, info.perimeter_m,
                info.centroid_lat, info.centroid_lon, "imported", 0, now, now,
            ),
        )
    return {
        "farm": _serialise_farm(_farm_row(farm_id)),
        "validation": info.as_dict(),
        "format": parsed["format"],
        "parse_warnings": parsed["warnings"],
        "overlaps": overlaps,
        "location": location,
        "warnings": info.warnings + parsed["warnings"],
    }


@router.post("/farms/sample", status_code=201)
def create_sample_farm() -> dict[str, Any]:
    """A clearly named sample boundary in rural Ashanti, analysed with real data."""
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [-0.9240, 10.8185], [-0.9195, 10.8182], [-0.9188, 10.8215],
            [-0.9210, 10.8232], [-0.9237, 10.8226], [-0.9240, 10.8185],
        ]],
    }
    body = FarmCreate(
        name="Sample farm — Bolgatanga area (Upper East)",
        geometry=geometry,
        crop_id="maize",
        irrigation="rainfed",
        notes=(
            "A sample boundary for exploring the app. It is a real location analysed with real sourced data; "
            "it is not a real farm boundary and belongs to no one in the app."
        ),
        source="sample",
        is_demo=False,
    )
    return create_farm(body)


# --------------------------------------------------------------------------- #
# Soil tests and records
# --------------------------------------------------------------------------- #
@router.post("/farms/{farm_id}/soil-tests", status_code=201)
def add_soil_test(farm_id: str, body: SoilTestCreate) -> dict[str, Any]:
    _farm_row(farm_id)
    test_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO soil_tests (id,farm_id,field_id,sampled_on,lab_name,depth_from_cm,depth_to_cm,ph,
            soc_gkg,total_n_gkg,p_mgkg,k_mgkg,cec_cmolkg,clay_pct,sand_pct,silt_pct,texture_class,source_url,
            notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                test_id, farm_id, body.field_id, body.sampled_on, body.lab_name, body.depth_from_cm,
                body.depth_to_cm, body.ph, body.soc_gkg, body.total_n_gkg, body.p_mgkg, body.k_mgkg,
                body.cec_cmolkg, body.clay_pct, body.sand_pct, body.silt_pct, body.texture_class,
                body.source_url, body.notes, now,
            ),
        )
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM soil_tests WHERE id=?", (test_id,)).fetchone()
    return {"soil_test": dict(row)}


@router.get("/farms/{farm_id}/soil-tests")
def list_soil_tests(farm_id: str) -> dict[str, Any]:
    _farm_row(farm_id)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM soil_tests WHERE farm_id=? ORDER BY sampled_on DESC", (farm_id,)
        ).fetchall()
    return {"soil_tests": [dict(row) for row in rows]}


@router.post("/farms/{farm_id}/records", status_code=201)
def add_record(farm_id: str, body: RecordCreate) -> dict[str, Any]:
    _farm_row(farm_id)
    record_id = uuid.uuid4().hex
    now = _now()
    recorded_at = body.recorded_at or now
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO farm_records (id,farm_id,field_id,kind,title,notes,payload,recorded_at,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                record_id, farm_id, body.field_id, body.kind, body.title, body.notes,
                json.dumps(body.payload) if body.payload else None, recorded_at, now,
            ),
        )
        conn.execute("UPDATE farms SET updated_at=? WHERE id=?", (now, farm_id))
        row = conn.execute("SELECT * FROM farm_records WHERE id=?", (record_id,)).fetchone()
    payload = dict(row)
    payload["payload"] = json.loads(row["payload"]) if row["payload"] else None
    return {"record": payload}


@router.get("/farms/{farm_id}/records")
def list_records(farm_id: str) -> dict[str, Any]:
    _farm_row(farm_id)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM farm_records WHERE farm_id=? ORDER BY recorded_at DESC", (farm_id,)
        ).fetchall()
    output = []
    for row in rows:
        payload = dict(row)
        payload["payload"] = json.loads(row["payload"]) if row["payload"] else None
        output.append(payload)
    return {"records": output}


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
@router.post("/farms/{farm_id}/analysis")
def start_analysis(farm_id: str, body: AnalysisRequest) -> dict[str, Any]:
    farm = _serialise_farm(_farm_row(farm_id), include_children=False)
    key = analysis_service.analysis_key(
        farm,
        body.field_id,
        body.crop_id or farm.get("crop_id"),
        body.planting_date or farm.get("planting_date"),
        body.management_level,
    )
    if not body.force:
        cached = analysis_service.get_cached(key)
        if cached:
            return {"cached": True, "analysis": cached, "job_id": None}
    job_id = analysis_service.submit_job(
        farm_id,
        {
            "field_id": body.field_id,
            "crop_id": body.crop_id,
            "planting_date": body.planting_date,
            "management_level": body.management_level,
        },
    )
    return {"cached": False, "job_id": job_id, "poll": f"/api/jobs/{job_id}"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = job_get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/farms/{farm_id}/analysis/latest")
def latest_analysis(farm_id: str) -> dict[str, Any]:
    _farm_row(farm_id)
    job = job_latest_for_farm(farm_id, "analysis")
    if job and job.get("status") == "complete" and job.get("result"):
        return {"analysis": job["result"], "job": {k: job[k] for k in ("id", "status", "updated_at")}}
    if job:
        return {"analysis": None, "job": {k: job[k] for k in ("id", "status", "progress", "message", "updated_at")}}
    return {"analysis": None, "job": None}


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
@router.get("/farms/{farm_id}/export")
def export_farm(farm_id: str, format: str = Query(default="geojson", pattern="^(geojson|kml|csv|pdf)$")):
    farm = _serialise_farm(_farm_row(farm_id))
    safe_name = "".join(char if char.isalnum() or char in "-_ " else "_" for char in farm["name"])[:60].strip() or "farm"
    if format == "geojson":
        payload = exports.farm_geojson(farm, _latest_payload(farm_id))
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.geojson"'},
        )
    if format == "kml":
        content = exports.farm_kml(farm)
        return Response(
            content,
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.kml"'},
        )
    if format == "csv":
        content = exports.farm_csv(farm)
        return Response(
            content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'},
        )
    analysis = _latest_payload(farm_id)
    if analysis is None:
        raise HTTPException(
            status_code=409,
            detail="Run 'View data for this area' before exporting a PDF so the report contains a real analysis.",
        )
    pdf = exports.farm_pdf(farm, analysis)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


def _latest_payload(farm_id: str) -> dict[str, Any] | None:
    job = job_latest_for_farm(farm_id, "analysis")
    if job and job.get("status") == "complete" and isinstance(job.get("result"), dict):
        return job["result"]
    return None
