"""Runtime configuration for Ghana Farm Intelligence.

All settings are read from environment variables so the same image runs locally
(Docker Compose) and on Railway (``$PORT`` + persistent volume).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

APP_NAME = "Ghana Farm Intelligence"
APP_SLUG = "ghana-farm-intelligence"
VERSION = "1.0.0"


def _ghana_timezone() -> tzinfo:
    """Africa/Accra, with a correct fallback.

    Windows has no IANA database unless the ``tzdata`` package is installed, and
    a missing database must not stop the app importing. ``ZoneInfo("UTC")`` is no
    help there either — it reads the same absent database — so the fallback is
    ``timezone.utc``. Accra is UTC+0 with no daylight saving: the same clock.
    """
    try:
        return ZoneInfo("Africa/Accra")
    except ZoneInfoNotFoundError:  # pragma: no cover - depends on the host
        return timezone.utc


GH_TZ = _ghana_timezone()

# Ghana national extent. Used for input validation and the default map view.
GHANA_BOUNDS = {
    "min_lat": 4.5,
    "max_lat": 11.2,
    "min_lon": -3.3,
    "max_lon": 1.25,
}
GHANA_CENTER = {"lat": 7.9465, "lon": -1.0232}
GHANA_AREA_KM2 = 238_533

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_DATA_DIR = BACKEND_ROOT / "data"
SEED_DIR = BUNDLED_DATA_DIR / "seed"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    env: str
    data_dir: Path
    static_dir: Path
    database_path: Path
    cache_dir: Path
    cors_origins: tuple[str, ...]
    admin_token: str
    access_token: str
    admin_user: str
    admin_password: str
    session_ttl_s: int
    cookie_secure: bool
    provider_timeout_s: float
    provider_retries: int
    allow_network: bool
    allow_demo_data: bool
    min_area_ha: float
    max_area_ha: float
    max_analysis_ha: float
    job_workers: int
    analysis_timeout_s: int
    analysis_cache_ttl_s: int
    weather_cache_ttl_s: int
    soil_cache_ttl_s: int
    rainfall_cache_ttl_s: int
    imagery_cache_ttl_s: int
    user_agent: str
    log_level: str

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def demo_enabled(self) -> bool:
        return self.allow_demo_data


def _load_settings() -> Settings:
    data_dir = Path(os.environ.get("GFI_DATA_DIR", str(BACKEND_ROOT / "data")))
    if not data_dir.is_absolute():
        data_dir = (BACKEND_ROOT / data_dir).resolve()
    db_path = Path(os.environ.get("GFI_DB_PATH", str(data_dir / "gfi.sqlite3")))
    if not db_path.is_absolute():
        db_path = (data_dir / db_path).resolve()
    static_dir = Path(os.environ.get("GFI_STATIC_DIR", str(BACKEND_ROOT.parent / "frontend" / "dist")))
    origins_raw = os.environ.get(
        "GFI_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )
    origins = tuple(o.strip() for o in origins_raw.split(",") if o.strip())
    return Settings(
        env=os.environ.get("GFI_ENV", "development"),
        data_dir=data_dir,
        static_dir=static_dir,
        database_path=db_path,
        cache_dir=data_dir / "cache",
        cors_origins=origins,
        admin_token=os.environ.get("GFI_ADMIN_TOKEN", ""),
        # One shared secret that gates the private API. Not per-user accounts: it
        # keeps a public URL from being world-readable, and stays off by default
        # so a laptop deployment needs no configuration.
        access_token=os.environ.get("GFI_ACCESS_TOKEN", ""),
        # First administrator for the accounts feature (see app/auth.py).
        admin_user=os.environ.get("GFI_ADMIN_USER", ""),
        admin_password=os.environ.get("GFI_ADMIN_PASSWORD", ""),
        session_ttl_s=_int("GFI_SESSION_TTL", 14 * 24 * 3600),
        # Set GFI_COOKIE_SECURE=1 when the app is served over HTTPS.
        cookie_secure=_bool("GFI_COOKIE_SECURE", False),
        provider_timeout_s=_float("GFI_PROVIDER_TIMEOUT", 45.0),
        provider_retries=_int("GFI_PROVIDER_RETRIES", 2),
        allow_network=_bool("GFI_ALLOW_NETWORK", True),
        allow_demo_data=_bool("GFI_ALLOW_DEMO_DATA", True),
        min_area_ha=_float("GFI_MIN_AREA_HA", 0.005),
        max_area_ha=_float("GFI_MAX_AREA_HA", 50_000.0),
        # A stored boundary may be large; reading it at 10 m is a different matter.
        # 20,000 ha is ~2 M pixels per Sentinel band.
        max_analysis_ha=_float("GFI_MAX_ANALYSIS_HA", 20_000.0),
        job_workers=_int("GFI_JOB_WORKERS", 3),
        # A job that exceeds this fails with a message instead of holding a worker
        # (and the UI's progress bar) indefinitely.
        analysis_timeout_s=_int("GFI_ANALYSIS_TIMEOUT", 300),
        analysis_cache_ttl_s=_int("GFI_ANALYSIS_CACHE_TTL", 3 * 3600),
        weather_cache_ttl_s=_int("GFI_WEATHER_CACHE_TTL", 30 * 60),
        soil_cache_ttl_s=_int("GFI_SOIL_CACHE_TTL", 30 * 24 * 3600),
        rainfall_cache_ttl_s=_int("GFI_RAINFALL_CACHE_TTL", 12 * 3600),
        imagery_cache_ttl_s=_int("GFI_IMAGERY_CACHE_TTL", 6 * 3600),
        user_agent="GhanaFarmIntelligence/1.0 (+https://github.com/ghana-farm-intelligence)",
        log_level=os.environ.get("GFI_LOG_LEVEL", "INFO"),
    )


settings = _load_settings()


def ensure_dirs() -> None:
    """Create the writable data directories (idempotent)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / "chips").mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / "providers").mkdir(parents=True, exist_ok=True)
