"""Accounts, passwords and sessions.

The model is deliberately small:

* an administrator creates accounts (there is no self-signup);
* each account has a username, a PBKDF2 password hash and an ``is_admin`` flag;
* a successful login sets an HttpOnly session cookie, stored as a SHA-256 hash in
  ``sessions`` with an expiry, so a leaked database does not leak live sessions;
* farms belong to the account that created them. Administrators can see and edit
  everything; other accounts see only their own farms.

Accounts are optional. While the ``users`` table is empty nothing changes: the
deployment is open unless ``GFI_ACCESS_TOKEN`` is set. Creating the first account
turns account auth on for the private API.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from contextlib import contextmanager

from .config import settings
from .db import get_conn

log = logging.getLogger(__name__)

SESSION_COOKIE = "gfi_session"
PBKDF2_ROUNDS = 200_000
USERNAME_MIN = 3
USERNAME_MAX = 40
PASSWORD_MIN = 8

# Set by the auth middleware for the duration of a request. One enforcement point
# (_farm_row / farm queries) reads it, so ownership cannot be forgotten in a new
# endpoint the way an explicit parameter can.
_CURRENT_USER: ContextVar[dict[str, Any] | None] = ContextVar("gfi_current_user", default=None)

# Cached "are there any accounts" flag; refreshed on startup and on every change.
_accounts_enabled = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt, digest = stored.split("$")
    except (AttributeError, ValueError):
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        expected = bytes.fromhex(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def validate_credentials(username: str, password: str) -> None:
    """Raise ValueError with a user-facing message for invalid input."""
    name = (username or "").strip()
    if len(name) < USERNAME_MIN or len(name) > USERNAME_MAX:
        raise ValueError(f"Usernames must be {USERNAME_MIN}–{USERNAME_MAX} characters.")
    if any(character.isspace() for character in name):
        raise ValueError("Usernames cannot contain spaces.")
    if len(password or "") < PASSWORD_MIN:
        raise ValueError(f"Passwords must be at least {PASSWORD_MIN} characters.")


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def accounts_enabled() -> bool:
    return _accounts_enabled


def refresh_accounts_flag() -> bool:
    global _accounts_enabled
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    _accounts_enabled = bool(row and row[0])
    return _accounts_enabled


def _user_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def find_user_by_username(username: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", ((username or "").strip(),)).fetchone()
    return dict(row) if row else None


def get_user(user_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        counts = {
            row["owner_id"]: row["count"]
            for row in conn.execute(
                "SELECT owner_id, COUNT(*) AS count FROM farms WHERE owner_id IS NOT NULL GROUP BY owner_id"
            ).fetchall()
        }
    output = []
    for row in rows:
        user = _user_row(row)
        user["farm_count"] = counts.get(user["id"], 0)
        output.append(user)
    return output


def create_user(username: str, password: str, *, is_admin: bool = False) -> dict[str, Any]:
    validate_credentials(username, password)
    name = username.strip()
    if find_user_by_username(name) is not None:
        raise ValueError(f"An account named “{name}” already exists.")
    user_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, name, hash_password(password), 1 if is_admin else 0, now, now),
        )
    refresh_accounts_flag()
    log.info("Created account %s (admin=%s)", name, is_admin)
    return _user_row(get_user(user_id))


def update_user(
    user_id: str,
    *,
    is_admin: bool | None = None,
    password: str | None = None,
    username: str | None = None,
) -> dict[str, Any] | None:
    updates: dict[str, Any] = {}
    if is_admin is not None:
        updates["is_admin"] = 1 if is_admin else 0
    if username is not None:
        validate_credentials(username, "x" * PASSWORD_MIN)  # username rules only
        new_name = username.strip()
        existing = find_user_by_username(new_name)
        if existing and existing["id"] != user_id:
            raise ValueError(f"An account named “{new_name}” already exists.")
        updates["username"] = new_name
    if password is not None:
        if len(password) < PASSWORD_MIN:
            raise ValueError(f"Passwords must be at least {PASSWORD_MIN} characters.")
        updates["password_hash"] = hash_password(password)
    if not updates:
        fresh = get_user(user_id)
        return _user_row(fresh) if fresh else None
    if updates.get("is_admin") == 0:
        _refuse_last_admin_removal(user_id)
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in updates)
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {assignments} WHERE id=?", (*updates.values(), user_id))
        if password is not None:
            # A password change signs the account out everywhere else.
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    fresh = get_user(user_id)
    # Same shape as create_user: booleans, no password_hash.
    return _user_row(fresh) if fresh else None


def delete_user(user_id: str) -> bool:
    user = get_user(user_id)
    if user is None:
        return False
    if user["is_admin"]:
        _refuse_last_admin_removal(user_id)
    with get_conn() as conn:
        # Keep the farms but unattach them: they become administrator-visible
        # only, instead of being silently destroyed with the account.
        conn.execute("UPDATE farms SET owner_id=NULL WHERE owner_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    refresh_accounts_flag()
    log.info("Deleted account %s", user["username"])
    return True


def _refuse_last_admin_removal(user_id: str) -> None:
    with get_conn() as conn:
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]
        target = conn.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if target and target[0] and admins <= 1:
        raise ValueError("This is the only administrator; make another account an administrator first.")


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(user_id: str) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_s)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (_token_hash(token), user_id, _now(), expires.isoformat(timespec="seconds")),
        )
    return token, expires


def end_session(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def user_for_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=? AND s.expires_at > ?",
            (_token_hash(token), _now()),
        ).fetchone()
    return _user_row(row) if row else None


def purge_expired_sessions() -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),)).rowcount


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #
def current_user() -> dict[str, Any] | None:
    return _CURRENT_USER.get()


@contextmanager
def acting_as(user: dict[str, Any] | None) -> Iterator[None]:
    token = _CURRENT_USER.set(user)
    try:
        yield
    finally:
        _CURRENT_USER.reset(token)


def can_see_farm(owner_id: str | None) -> bool:
    """Whether the acting account may read or modify a farm.

    With accounts disabled every request is trusted (the deployment is either
    local or gated by GFI_ACCESS_TOKEN). With accounts enabled an administrator
    sees everything; anyone else sees only their own farms. Farms with no owner
    predate accounts and are administrator-only.
    """
    if not accounts_enabled():
        return True
    user = current_user()
    if user is None:
        return False
    return bool(user["is_admin"]) or (owner_id is not None and owner_id == user["id"])


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def ensure_bootstrap_admin() -> None:
    """Create the first administrator from the environment, if configured.

    This is the chicken-and-egg answer: an administrator has to exist before
    accounts can be managed in the UI, and an environment variable is the only
    channel available on a fresh deployment.
    """
    refresh_accounts_flag()
    purge_expired_sessions()
    username = (settings.admin_user or "").strip()
    password = settings.admin_password or ""
    if not username or not password:
        if not accounts_enabled():
            log.info(
                "Accounts are disabled (no users). Set GFI_ADMIN_USER and GFI_ADMIN_PASSWORD to create "
                "the first administrator."
            )
        return
    if len(password) < PASSWORD_MIN:
        log.warning("GFI_ADMIN_PASSWORD is shorter than %s characters; refusing to create the account.", PASSWORD_MIN)
        return
    existing = find_user_by_username(username)
    try:
        if existing is None:
            create_user(username, password, is_admin=True)
            log.info("Created the bootstrap administrator %s", username)
        elif not existing["is_admin"]:
            update_user(existing["id"], is_admin=True)
            log.info("Promoted the bootstrap account %s to administrator", username)
    except ValueError as exc:
        log.warning("Could not create the bootstrap administrator: %s", exc)
