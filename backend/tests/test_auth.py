"""Accounts, sign-in and farm ownership.

These tests create real accounts, so they clean up after themselves: later test
modules assume a deployment with accounts disabled.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.db import get_conn
from app.main import app

FARM = {
    "type": "Polygon",
    "coordinates": [[
        [-1.4542, 6.6974], [-1.4494, 6.6971], [-1.4482, 6.7001],
        [-1.4507, 6.7024], [-1.4539, 6.7017], [-1.4542, 6.6974],
    ]],
}


def _delete_all_accounts() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
    auth.refresh_accounts_flag()


@pytest.fixture
def accounts():
    """A clean slate: no accounts and no farms left by earlier test modules."""
    _delete_all_accounts()
    with get_conn() as conn:
        conn.execute("DELETE FROM farm_fields")
        conn.execute("DELETE FROM farms")
        conn.execute("DELETE FROM jobs")
    try:
        yield
    finally:
        _delete_all_accounts()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _sign_in(client: TestClient, username: str, password: str) -> TestClient:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _farm(client: TestClient, name: str) -> dict:
    response = client.post("/api/farms", json={"name": name, "geometry": FARM, "crop_id": "maize"})
    assert response.status_code == 201, response.text
    return response.json()["farm"]


def test_accounts_are_off_until_one_exists(client, accounts):
    payload = client.get("/api/auth/me").json()
    assert payload["accounts_enabled"] is False
    assert payload["authenticated"] is False
    # No accounts: the open deployment behaves exactly as before.
    assert client.get("/api/farms").status_code == 200
    # Account management is refused rather than silently unprotected.
    assert client.get("/api/auth/users").status_code == 409
    assert client.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 409


def test_creating_the_first_account_closes_the_api(client, accounts):
    auth.create_user("root", "rootpass123", is_admin=True)
    assert client.get("/api/farms").status_code == 401
    assert client.post("/api/auth/login", json={"username": "root", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"}).status_code == 401
    _sign_in(client, "root", "rootpass123")
    assert client.get("/api/farms").status_code == 200


def test_admin_creates_accounts_and_sets_the_admin_flag(client, accounts):
    auth.create_user("root", "rootpass123", is_admin=True)
    _sign_in(client, "root", "rootpass123")

    created = client.post(
        "/api/auth/users",
        json={"username": "amina", "password": "fieldwork99", "is_admin": False},
    )
    assert created.status_code == 201, created.text
    assert created.json()["user"]["is_admin"] is False
    assert "password" not in created.json()["user"]
    assert "password_hash" not in created.json()["user"]

    promoted = client.patch(f"/api/auth/users/{created.json()['user']['id']}", json={"is_admin": True})
    assert promoted.status_code == 200
    assert promoted.json()["user"]["is_admin"] is True

    listed = client.get("/api/auth/users").json()["users"]
    assert {user["username"] for user in listed} == {"root", "amina"}


def test_duplicate_usernames_are_rejected_case_insensitively(client, accounts):
    auth.create_user("root", "rootpass123", is_admin=True)
    _sign_in(client, "root", "rootpass123")
    client.post("/api/auth/users", json={"username": "Amina", "password": "fieldwork99"})
    duplicate = client.post("/api/auth/users", json={"username": "amina", "password": "fieldwork99"})
    assert duplicate.status_code == 422
    assert "already exists" in duplicate.json()["detail"]


def test_non_admin_cannot_manage_accounts(client, accounts):
    auth.create_user("root", "rootpass123", is_admin=True)
    auth.create_user("amina", "fieldwork99", is_admin=False)
    _sign_in(client, "amina", "fieldwork99")
    assert client.get("/api/auth/users").status_code == 403
    assert client.post("/api/auth/users", json={"username": "xavier", "password": "fieldwork99"}).status_code == 403


def test_farms_are_private_to_their_owner(client, accounts):
    root = auth.create_user("root", "rootpass123", is_admin=True)
    amina = auth.create_user("amina", "fieldwork99", is_admin=False)

    _sign_in(client, "root", "rootpass123")
    admin_farm = _farm(client, "Admin field")

    client.cookies.clear()
    _sign_in(client, "amina", "fieldwork99")
    user_farm = _farm(client, "Amina field")

    listed = client.get("/api/farms").json()["farms"]
    assert [item["name"] for item in listed] == ["Amina field"]
    assert client.get(f"/api/farms/{admin_farm['id']}").status_code == 404
    assert client.delete(f"/api/farms/{admin_farm['id']}").status_code == 404
    assert client.post(f"/api/farms/{admin_farm['id']}/analysis", json={}).status_code == 404

    client.cookies.clear()
    _sign_in(client, "root", "rootpass123")
    names = {item["name"] for item in client.get("/api/farms").json()["farms"]}
    assert names == {"Admin field", "Amina field"}
    assert client.get(f"/api/farms/{user_farm['id']}").status_code == 200

    # The owner keeps their farm on record.
    assert amina["id"] != root["id"]


def test_last_administrator_cannot_be_removed_or_deleted(client, accounts):
    root = auth.create_user("root", "rootpass123", is_admin=True)
    _sign_in(client, "root", "rootpass123")

    assert client.patch(f"/api/auth/users/{root['id']}", json={"is_admin": False}).status_code == 422
    assert client.delete(f"/api/auth/users/{root['id']}").status_code == 422

    other = client.post("/api/auth/users", json={"username": "second", "password": "rootpass123", "is_admin": True})
    assert other.status_code == 201
    # With a second administrator, self-demotion is still refused in the UI path
    # but another administrator can be demoted.
    assert client.patch(f"/api/auth/users/{other.json()['user']['id']}", json={"is_admin": False}).status_code == 200


def test_password_change_ends_other_sessions(client, accounts):
    root = auth.create_user("root", "rootpass123", is_admin=True)
    auth.create_user("amina", "fieldwork99", is_admin=False)

    _sign_in(client, "root", "rootpass123")
    amina_id = next(user["id"] for user in client.get("/api/auth/users").json()["users"] if user["username"] == "amina")
    assert client.patch(f"/api/auth/users/{amina_id}", json={"password": "newpass123"}).status_code == 200

    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "amina", "password": "fieldwork99"}).status_code == 401
    _sign_in(client, "amina", "newpass123")

    # The old session was deleted with the password change.
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/farms").status_code == 401
    assert root["is_admin"] is True


def test_deleting_an_account_keeps_its_farms_for_the_administrator(client, accounts):
    auth.create_user("root", "rootpass123", is_admin=True)
    victim = auth.create_user("leaver", "leaverpass1", is_admin=False)

    _sign_in(client, "leaver", "leaverpass1")
    _farm(client, "Orphan field")

    client.cookies.clear()
    _sign_in(client, "root", "rootpass123")
    assert client.delete(f"/api/auth/users/{victim['id']}").status_code == 204
    names = {item["name"] for item in client.get("/api/farms").json()["farms"]}
    assert "Orphan field" in names
