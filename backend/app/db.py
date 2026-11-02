"""SQLite persistence layer (local-first MVP; migrate to PostGIS if multi-user).

Design notes
------------
* SQLite in WAL mode, one connection per request (cheap and safe).
* Small forward-only migration list keyed on ``PRAGMA user_version``.
* Provider responses are cached in ``provider_cache`` with a per-source TTL so a
  failed provider can fall back to the last successful (but clearly dated) value.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import ensure_dirs, settings

SCHEMA_VERSION = 1

MIGRATIONS: list[str] = [
    # ---- 1: initial schema -------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS farms (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        region          TEXT,
        district        TEXT,
        zone            TEXT,
        crop_id         TEXT,
        crop_variety    TEXT,
        planting_date   TEXT,
        irrigation      TEXT,
        notes           TEXT,
        geometry        TEXT NOT NULL,
        area_ha         REAL NOT NULL,
        perimeter_m     REAL NOT NULL,
        centroid_lat    REAL,
        centroid_lon    REAL,
        source          TEXT NOT NULL DEFAULT 'drawn',
        is_demo         INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_farms_updated ON farms(updated_at DESC);

    CREATE TABLE IF NOT EXISTS farm_fields (
        id              TEXT PRIMARY KEY,
        farm_id         TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
        name            TEXT NOT NULL,
        crop_id         TEXT,
        crop_variety    TEXT,
        planting_date   TEXT,
        irrigation      TEXT,
        geometry        TEXT NOT NULL,
        area_ha         REAL NOT NULL,
        perimeter_m     REAL NOT NULL,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fields_farm ON farm_fields(farm_id);

    CREATE TABLE IF NOT EXISTS farm_records (
        id              TEXT PRIMARY KEY,
        farm_id         TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
        field_id        TEXT REFERENCES farm_fields(id) ON DELETE SET NULL,
        kind            TEXT NOT NULL,
        title           TEXT,
        notes           TEXT,
        payload         TEXT,
        recorded_at     TEXT NOT NULL,
        created_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_records_farm ON farm_records(farm_id, recorded_at DESC);

    CREATE TABLE IF NOT EXISTS soil_tests (
        id              TEXT PRIMARY KEY,
        farm_id         TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
        field_id        TEXT REFERENCES farm_fields(id) ON DELETE SET NULL,
        sampled_on      TEXT NOT NULL,
        lab_name        TEXT,
        depth_from_cm   INTEGER,
        depth_to_cm     INTEGER,
        ph              REAL,
        soc_gkg         REAL,
        total_n_gkg     REAL,
        p_mgkg          REAL,
        k_mgkg          REAL,
        cec_cmolkg      REAL,
        clay_pct        REAL,
        sand_pct        REAL,
        silt_pct        REAL,
        texture_class   TEXT,
        source_url      TEXT,
        notes           TEXT,
        created_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_soiltests_farm ON soil_tests(farm_id, sampled_on DESC);

    CREATE TABLE IF NOT EXISTS advisories (
        id              TEXT PRIMARY KEY,
        title           TEXT NOT NULL,
        body            TEXT NOT NULL,
        agency          TEXT NOT NULL DEFAULT 'GMet',
        source_url      TEXT NOT NULL,
        published_at    TEXT,
        valid_from      TEXT,
        valid_to        TEXT,
        regions         TEXT,
        crop_ids        TEXT,
        created_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS market_prices (
        id              TEXT PRIMARY KEY,
        district        TEXT,
        market          TEXT,
        crop_id         TEXT NOT NULL,
        price_ghs       REAL NOT NULL,
        unit            TEXT NOT NULL,
        observed_on     TEXT NOT NULL,
        source_note     TEXT,
        created_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS provider_cache (
        cache_key       TEXT PRIMARY KEY,
        provider        TEXT NOT NULL,
        payload         TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        expires_at      TEXT NOT NULL,
        hits            INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_cache_expiry ON provider_cache(expires_at);

    CREATE TABLE IF NOT EXISTS provider_health (
        provider_id     TEXT PRIMARY KEY,
        status          TEXT NOT NULL,
        checked_at      TEXT NOT NULL,
        latency_ms      INTEGER,
        message         TEXT,
        detail          TEXT
    );

    CREATE TABLE IF NOT EXISTS analysis_cache (
        cache_key       TEXT PRIMARY KEY,
        farm_id         TEXT,
        payload         TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        expires_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_analysis_farm ON analysis_cache(farm_id);

    CREATE TABLE IF NOT EXISTS jobs (
        id              TEXT PRIMARY KEY,
        farm_id         TEXT,
        kind            TEXT NOT NULL,
        status          TEXT NOT NULL,
        progress        REAL NOT NULL DEFAULT 0,
        message         TEXT,
        result          TEXT,
        error           TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_farm ON jobs(farm_id, created_at DESC);

    CREATE TABLE IF NOT EXISTS place_index (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        type            TEXT,
        lat             REAL NOT NULL,
        lon             REAL NOT NULL,
        region          TEXT,
        district        TEXT,
        population      INTEGER,
        search_text     TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_place_name ON place_index(name);
    """,
    # ---- 2: accounts, sessions and farm ownership -------------------------
    """
    CREATE TABLE IF NOT EXISTS users (
        id              TEXT PRIMARY KEY,
        username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash   TEXT NOT NULL,
        is_admin        INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
        token_hash      TEXT PRIMARY KEY,
        user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at      TEXT NOT NULL,
        expires_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

    ALTER TABLE farms ADD COLUMN owner_id TEXT;
    CREATE INDEX IF NOT EXISTS idx_farms_owner ON farms(owner_id);
    """,
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(
        settings.database_path,
        timeout=30.0,
        isolation_level=None,  # autocommit; explicit transactions where needed
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Apply pending migrations and load the seed place index if present."""
    ensure_dirs()
    with get_conn() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(version, len(MIGRATIONS)):
            conn.executescript(MIGRATIONS[index])
            conn.execute(f"PRAGMA user_version={index + 1}")
    _load_place_seed()


def _load_place_seed() -> None:
    """Populate the local locality index from the bundled OSM seed once."""
    seed_file = settings.data_dir / "seed" / "ghana_places.json.gz"
    if not seed_file.exists():
        seed_file = __import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "seed" / "ghana_places.json.gz"
    if not seed_file.exists():
        return
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM place_index").fetchone()[0]
        if count > 0:
            return
        import gzip

        with gzip.open(seed_file, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        rows = []
        for place in payload.get("places", []):
            name = (place.get("name") or "").strip()
            if not name:
                continue
            region = place.get("region")
            district = place.get("district")
            search_text = " ".join(x for x in [name, district or "", region or ""] if x).lower()
            rows.append(
                (
                    place.get("id") or f"seed:{name}:{place.get('lat')}:{place.get('lon')}",
                    name,
                    place.get("type"),
                    float(place.get("lat")),
                    float(place.get("lon")),
                    region,
                    district,
                    place.get("population"),
                    search_text,
                )
            )
        conn.executemany(
            "INSERT OR IGNORE INTO place_index (id,name,type,lat,lon,region,district,population,search_text)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )


# --------------------------------------------------------------------------- #
# Provider cache helpers
# --------------------------------------------------------------------------- #
def cache_get(key: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT provider,payload,created_at,expires_at FROM provider_cache WHERE cache_key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE provider_cache SET hits=hits+1 WHERE cache_key=?", (key,))
    return {
        "provider": row["provider"],
        "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "expired": row["expires_at"] < _now(),
    }


def cache_put(key: str, provider: str, payload: Any, ttl_seconds: int) -> None:
    now = datetime.now(timezone.utc)
    expires = (now + __import__("datetime").timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
    blob = json.dumps(payload, default=str)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO provider_cache (cache_key,provider,payload,created_at,expires_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET"
            " payload=excluded.payload, created_at=excluded.created_at, expires_at=excluded.expires_at",
            (key, provider, blob, now.isoformat(timespec="seconds"), expires),
        )


def cache_prune(keep_expired_days: int = 45) -> int:
    cutoff = (
        datetime.now(timezone.utc) - __import__("datetime").timedelta(days=keep_expired_days)
    ).isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM provider_cache WHERE expires_at < ?", (cutoff,))
        return cur.rowcount


def housekeeping(keep_expired_days: int = 45, keep_finished_jobs_days: int = 30) -> dict[str, int]:
    """Fail jobs orphaned by a restart and delete expired cache rows.

    Jobs run in-process on a bounded thread pool, so nothing left ``queued`` or
    ``running`` when the process starts can ever finish; the UI would poll it
    forever. Called from the application lifespan.
    """
    now = datetime.now(timezone.utc)
    cache_cutoff = (now - __import__("datetime").timedelta(days=keep_expired_days)).isoformat(timespec="seconds")
    job_cutoff = (now - __import__("datetime").timedelta(days=keep_finished_jobs_days)).isoformat(timespec="seconds")
    with get_conn() as conn:
        interrupted = conn.execute(
            "UPDATE jobs SET status='failed', message='Interrupted by a restart', error='interrupted', updated_at=?"
            " WHERE status IN ('queued','running')",
            (_now(),),
        ).rowcount
        deleted_jobs = conn.execute(
            "DELETE FROM jobs WHERE status IN ('complete','failed') AND updated_at < ?", (job_cutoff,)
        ).rowcount
        provider_deleted = conn.execute(
            "DELETE FROM provider_cache WHERE expires_at < ?", (cache_cutoff,)
        ).rowcount
        analysis_deleted = conn.execute(
            "DELETE FROM analysis_cache WHERE expires_at < ?", (cache_cutoff,)
        ).rowcount
    return {
        "interrupted_jobs": interrupted,
        "deleted_jobs": deleted_jobs,
        "deleted_provider_cache": provider_deleted,
        "deleted_analysis_cache": analysis_deleted,
    }


def record_provider_health(provider_id: str, status: str, latency_ms: int | None,
                           message: str | None = None, detail: Any = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO provider_health (provider_id,status,checked_at,latency_ms,message,detail)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET"
            " status=excluded.status, checked_at=excluded.checked_at, latency_ms=excluded.latency_ms,"
            " message=excluded.message, detail=excluded.detail",
            (provider_id, status, _now(), latency_ms, message, json.dumps(detail, default=str) if detail else None),
        )


def provider_health_all() -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM provider_health").fetchall()
    out = {}
    for row in rows:
        detail = None
        if row["detail"]:
            try:
                detail = json.loads(row["detail"])
            except json.JSONDecodeError:
                detail = row["detail"]
        out[row["provider_id"]] = {
            "status": row["status"],
            "checked_at": row["checked_at"],
            "latency_ms": row["latency_ms"],
            "message": row["message"],
            "detail": detail,
        }
    return out


# --------------------------------------------------------------------------- #
# Analysis cache
# --------------------------------------------------------------------------- #
def analysis_cache_get(key: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload,created_at,expires_at FROM analysis_cache WHERE cache_key=?", (key,)
        ).fetchone()
    if row is None:
        return None
    return {
        "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "expired": row["expires_at"] < _now(),
    }


def analysis_cache_put(key: str, farm_id: str | None, payload: Any, ttl_seconds: int) -> None:
    now = datetime.now(timezone.utc)
    expires = (now + __import__("datetime").timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO analysis_cache (cache_key,farm_id,payload,created_at,expires_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,"
            " created_at=excluded.created_at, expires_at=excluded.expires_at",
            (key, farm_id, json.dumps(payload, default=str), now.isoformat(timespec="seconds"), expires),
        )


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
def job_create(job_id: str, farm_id: str | None, kind: str) -> None:
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id,farm_id,kind,status,progress,message,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (job_id, farm_id, kind, "queued", 0.0, "Queued", now, now),
        )


def job_update(job_id: str, **fields: Any) -> None:
    allowed = {"status", "progress", "message", "result", "error"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "result" in updates and not isinstance(updates["result"], str):
        updates["result"] = json.dumps(updates["result"], default=str)
    assignments = ", ".join(f"{key}=?" for key in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments}, updated_at=? WHERE id=?",
            (*updates.values(), _now(), job_id),
        )


def job_get(job_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    result = None
    if row["result"]:
        try:
            result = json.loads(row["result"])
        except json.JSONDecodeError:
            result = row["result"]
    return {
        "id": row["id"],
        "farm_id": row["farm_id"],
        "kind": row["kind"],
        "status": row["status"],
        "progress": row["progress"],
        "message": row["message"],
        "result": result,
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def job_latest_for_farm(farm_id: str, kind: str = "analysis") -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE farm_id=? AND kind=? ORDER BY created_at DESC LIMIT 1",
            (farm_id, kind),
        ).fetchone()
    if row is None:
        return None
    return job_get(row["id"])
