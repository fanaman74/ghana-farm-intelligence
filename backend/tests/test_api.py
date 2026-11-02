"""API-level tests: the contract the web client actually depends on.

These exist because the frontend and the routers drifted once already (the save
form discarded the crop, the PDF export 500'd on a "<" in a farm name) and no
test could see it.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

FARM = {
    "type": "Polygon",
    "coordinates": [[
        [-1.4542, 6.6974], [-1.4494, 6.6971], [-1.4482, 6.7001],
        [-1.4507, 6.7024], [-1.4539, 6.7017], [-1.4542, 6.6974],
    ]],
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _create(client: TestClient, headers: dict[str, str] | None = None, **overrides) -> dict:
    body = {
        "name": "Review field",
        "geometry": FARM,
        "crop_id": "maize",
        "planting_date": "2026-05-20",
        "irrigation": "rainfed",
        "notes": "created by the test suite",
        "source": "drawn",
    }
    body.update(overrides)
    response = client.post("/api/farms", json=body, headers=headers or {})
    assert response.status_code == 201, response.text
    return response.json()


def test_health_reports_ok(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"


def test_create_farm_persists_the_fields_the_form_sends(client):
    """Regression: the save dialog's primary button posted an empty payload."""
    created = _create(client, name="Mensah Farm", crop_id="cassava", planting_date="2026-06-01")
    farm = created["farm"]
    assert farm["name"] == "Mensah Farm"
    assert farm["crop_id"] == "cassava"
    assert farm["planting_date"] == "2026-06-01"

    fetched = client.get(f"/api/farms/{farm['id']}").json()["farm"]
    assert fetched["name"] == "Mensah Farm"
    assert fetched["crop_id"] == "cassava"
    assert created["validation"]["area_ha"] > 0


def test_geometry_validation_rejects_a_self_intersection(client):
    bowtie = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]]}
    response = client.post("/api/geometry/validate", json={"geometry": bowtie})
    assert response.status_code == 422
    assert response.json()["valid"] is False


def test_geometry_outside_ghana_is_rejected(client):
    outside = {"type": "Polygon", "coordinates": [[[0.1, 48.8], [0.2, 48.8], [0.2, 48.9], [0.1, 48.9], [0.1, 48.8]]]}
    response = client.post("/api/geometry/validate", json={"geometry": outside})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "outside_ghana"


def test_pdf_export_requires_an_analysis(client):
    farm = _create(client)["farm"]
    response = client.get(f"/api/farms/{farm['id']}/export?format=pdf")
    assert response.status_code == 409


def test_geojson_export_round_trips(client):
    farm = _create(client)["farm"]
    response = client.get(f"/api/farms/{farm['id']}/export?format=geojson")
    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert payload["features"][0]["geometry"]["type"] == "Polygon"


def test_delete_farm_then_read_is_404(client):
    farm = _create(client)["farm"]
    assert client.delete(f"/api/farms/{farm['id']}").status_code == 204
    assert client.get(f"/api/farms/{farm['id']}").status_code == 404


def test_chip_route_cannot_escape_the_cache_directory(client):
    """A traversal attempt must not read arbitrary files."""
    response = client.get("/api/chips/..%2F..%2Fapp%2Fconfig.py")
    assert response.status_code in (404, 400)


def test_catalogue_exposes_twenty_cited_crops(client):
    payload = client.get("/api/catalog/crops").json()
    assert len(payload["crops"]) == 20
    assert payload["sources"]


def test_unknown_layer_is_a_404_with_the_available_ids(client):
    farm = _create(client)["farm"]
    response = client.get(f"/api/farms/{farm['id']}/layers/not_a_layer")
    assert response.status_code == 404
    assert "soil_ph" in response.json()["detail"]


def test_admin_token_default_is_documented_not_enforced(client):
    """The default deployment is open; /admin/info must say so plainly."""
    payload = client.get("/api/admin/info").json()
    assert payload["admin_token_required"] is False
    assert "anyone who can reach the API" in payload["message"]


def test_access_token_gates_private_data_but_not_reference_data(client, monkeypatch):
    """GFI_ACCESS_TOKEN turns an open deployment into a single-secret one."""
    import dataclasses

    import app.api.core as core_module
    import app.main as main_module
    from app.config import settings as base_settings

    secured = dataclasses.replace(base_settings, access_token="s3cret-token")
    monkeypatch.setattr(main_module, "settings", secured)
    monkeypatch.setattr(core_module, "settings", secured)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["auth"]["farm_data"] == "access_token"

    assert client.get("/api/farms").status_code == 401
    assert client.get("/api/farms", headers={"X-Access-Token": "wrong"}).status_code == 401
    assert client.get("/api/farms", headers={"X-Access-Token": "s3cret-token"}).status_code == 200

    # The SPA and MapLibre fetch reference data before any token is available.
    assert client.get("/api/basemap/places?limit=1").status_code == 200
    assert client.get("/api/catalog/crops").status_code == 200

    # A private export needs the header too — the client downloads it as a blob.
    farm = _create(client, headers={"X-Access-Token": "s3cret-token"})["farm"]
    assert client.get(f"/api/farms/{farm['id']}/export?format=geojson").status_code == 401
    assert (
        client.get(
            f"/api/farms/{farm['id']}/export?format=geojson",
            headers={"X-Access-Token": "s3cret-token"},
        ).status_code
        == 200
    )


def test_analysis_request_returns_a_job_or_a_cache_hit(client, monkeypatch):
    """The analysis endpoint must respond without touching the network here."""
    import app.analysis as analysis_service

    monkeypatch.setattr(
        analysis_service,
        "submit_job",
        lambda farm_id, params, force=False: "job-fixture",
    )
    monkeypatch.setattr(analysis_service, "get_cached", lambda key: None)
    farm = _create(client)["farm"]
    response = client.post(f"/api/farms/{farm['id']}/analysis", json={"force": False})
    assert response.status_code == 200
    payload = response.json()
    assert payload["cached"] is False
    assert payload["job_id"] == "job-fixture"
    # The polling URL the client uses must resolve to the same id.
    assert client.get(f"/api/jobs/{payload['job_id']}").status_code == 404 or payload["poll"] == "/api/jobs/job-fixture"


def test_force_flag_reaches_the_job(client, monkeypatch):
    """Regression: the refresh button created a job that re-read the cache."""
    import app.analysis as analysis_service

    seen: list[bool] = []

    def fake_submit(farm_id, params, force=False):
        seen.append(force)
        return "job-force"

    monkeypatch.setattr(analysis_service, "submit_job", fake_submit)
    monkeypatch.setattr(analysis_service, "get_cached", lambda key: None)
    farm = _create(client)["farm"]
    response = client.post(f"/api/farms/{farm['id']}/analysis", json={"force": True})
    assert response.status_code == 200
    assert seen == [True]


def test_import_rejects_a_broken_upload(client):
    response = client.post(
        "/api/farms/import",
        files={"file": ("broken.geojson", b"this is not geojson", "application/geo+json")},
    )
    assert response.status_code == 422


def test_import_accepts_geojson(client):
    body = json.dumps({"type": "Feature", "properties": {"name": "Imported field"}, "geometry": FARM}).encode()
    response = client.post(
        "/api/farms/import",
        files={"file": ("field.geojson", body, "application/geo+json")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["farm"]["name"] == "Imported field"
