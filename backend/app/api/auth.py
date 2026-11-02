"""Sign-in, sign-out and administrator account management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..config import settings

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class UserCreate(BaseModel):
    username: str = Field(min_length=auth.USERNAME_MIN, max_length=auth.USERNAME_MAX)
    password: str = Field(min_length=auth.PASSWORD_MIN, max_length=200)
    is_admin: bool = False


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=auth.USERNAME_MIN, max_length=auth.USERNAME_MAX)
    password: str | None = Field(default=None, min_length=auth.PASSWORD_MIN, max_length=200)
    is_admin: bool | None = None


def _require_admin() -> dict[str, Any]:
    """Account management is administrator-only, and only once accounts exist."""
    if not auth.accounts_enabled():
        raise HTTPException(
            status_code=409,
            detail=(
                "Accounts are not enabled on this deployment. Set GFI_ADMIN_USER and GFI_ADMIN_PASSWORD "
                "to create the first administrator."
            ),
        )
    user = auth.current_user()
    if user is None or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_s,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


@router.post("/auth/login")
def login(body: LoginRequest, response: Response) -> dict[str, Any]:
    if not auth.accounts_enabled():
        raise HTTPException(
            status_code=409,
            detail=(
                "Accounts are not enabled on this deployment. Set GFI_ADMIN_USER and GFI_ADMIN_PASSWORD "
                "to create the first administrator."
            ),
        )
    record = auth.find_user_by_username(body.username)
    # One message for an unknown user and a wrong password: the response must not
    # reveal which usernames exist.
    if record is None or not auth.verify_password(body.password, record["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    token, expires = auth.start_session(record["id"])
    _set_session_cookie(response, token)
    return {
        "authenticated": True,
        "user": {key: record[key] for key in ("id", "username", "is_admin", "created_at")},
        "expires_at": expires.isoformat(timespec="seconds"),
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        auth.end_session(token)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.get("/auth/me")
def me(request: Request) -> dict[str, Any]:
    """Who is signed in. Public, so the app can show the sign-in screen."""
    user = auth.user_for_token(request.cookies.get(auth.SESSION_COOKIE))
    return {
        "authenticated": user is not None,
        "accounts_enabled": auth.accounts_enabled(),
        "user": user,
        "posture": "accounts" if auth.accounts_enabled() else ("token" if settings.access_token else "open"),
    }


@router.get("/auth/users")
def list_users() -> dict[str, Any]:
    _require_admin()
    return {"users": auth.list_users()}


@router.post("/auth/users", status_code=201)
def create_user(body: UserCreate) -> dict[str, Any]:
    _require_admin()
    try:
        return {"user": auth.create_user(body.username, body.password, is_admin=body.is_admin)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/auth/users/{user_id}")
def update_user(user_id: str, body: UserUpdate) -> dict[str, Any]:
    actor = _require_admin()
    if user_id == actor["id"] and body.is_admin is False:
        raise HTTPException(status_code=422, detail="You cannot remove your own administrator rights.")
    try:
        user = auth.update_user(
            user_id,
            is_admin=body.is_admin,
            password=body.password,
            username=body.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return {"user": user}


@router.delete("/auth/users/{user_id}", status_code=204)
def delete_user(user_id: str) -> Response:
    actor = _require_admin()
    if user_id == actor["id"]:
        raise HTTPException(status_code=422, detail="You cannot delete the account you are signed in as.")
    try:
        deleted = auth.delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Account not found.")
    return Response(status_code=204)
