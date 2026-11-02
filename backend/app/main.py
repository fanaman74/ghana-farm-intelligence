"""Ghana Farm Intelligence — FastAPI application.

Serves the JSON API under /api and the built React PWA from the same origin, so
the app runs on one port locally, on a LAN, or on Railway behind HTTPS.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import admin as admin_router
from .api import auth as auth_router
from .api import core as core_router
from .api import farms as farms_router
from .api import layers as layers_router
from .config import APP_NAME, VERSION, ensure_dirs, settings
from .db import housekeeping, init_db
from .geo.geodesy import GeometryError
from .providers.base import close_client
from . import auth

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("gfi")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    housekeeping_report = housekeeping()
    log.info(
        "Ghana Farm Intelligence %s starting (env=%s, data=%s)",
        VERSION,
        settings.env,
        settings.data_dir,
    )
    log.info("Housekeeping: %s", housekeeping_report)
    auth.ensure_bootstrap_admin()
    if settings.is_production and not settings.access_token and not auth.accounts_enabled():
        log.warning(
            "GFI_ENV=production with no accounts and no GFI_ACCESS_TOKEN. Every boundary, record and "
            "analysis is readable and deletable by anyone who can reach the service. Create an "
            "administrator with GFI_ADMIN_USER/GFI_ADMIN_PASSWORD, or set GFI_ACCESS_TOKEN."
        )
    yield
    close_client()


app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "Polygon-based farm intelligence for Ghana. Real provider data with explicit provenance, "
        "resolution, uncertainty and limitations."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) or [],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
# Compress text responses for low-bandwidth phones. Already-gzipped seed files
# set Content-Encoding themselves and are skipped by Starlette's middleware.
app.add_middleware(GZipMiddleware, minimum_size=800, compresslevel=6)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    """Refuse an oversized import before the body is read.

    Starlette spools the request body before the handler runs, so a cap inside the
    handler is too late to protect memory. Content-Length is advisory — the
    chunked read in the handler is the real guard — but it is the only cheap
    early signal.
    """
    if request.url.path.endswith("/import"):
        raw = request.headers.get("content-length")
        if raw and raw.isdigit() and int(raw) > 12 * 1024 * 1024 + 4096:
            return JSONResponse(status_code=413, content={"detail": "File is larger than 12 MB"})
    return await call_next(request)


# Reference data, health and rendered PNGs stay readable without signing in: the
# SPA needs them before it can ask for a password, and MapLibre fetches them with
# its own headers. Everything else under /api is private.
PUBLIC_API_PATHS = {
    "/api/health",
    "/api/meta",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}
PUBLIC_API_PREFIXES = (
    "/api/chips/",
    "/api/basemap/",
    "/api/geo/",
    "/api/catalog/",
    "/api/sources/",
    "/api/places/",
    "/api/docs",
    "/api/openapi.json",
)


def _is_public(path: str) -> bool:
    return path in PUBLIC_API_PATHS or path.startswith(PUBLIC_API_PREFIXES)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    """Gate the private API: accounts first, then the shared token.

    * With accounts enabled, a session cookie is required and the signed-in
      account is available to the endpoints (farm ownership is enforced there).
    * With no accounts, ``GFI_ACCESS_TOKEN`` gates the same routes if it is set.
    * With neither, the deployment is open — the documented local default.
    """
    path = request.url.path
    if not path.startswith("/api/") or _is_public(path):
        return await call_next(request)

    if auth.accounts_enabled():
        user = auth.user_for_token(request.cookies.get(auth.SESSION_COOKIE))
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Sign in to continue.", "auth": "accounts"},
            )
        with auth.acting_as(user):
            return await call_next(request)

    token = settings.access_token
    if token:
        supplied = request.headers.get("x-access-token") or ""
        if not secrets.compare_digest(supplied, token):
            return JSONResponse(
                status_code=401,
                content={"detail": "This deployment requires an access token (X-Access-Token header)."},
                headers={"WWW-Authenticate": "Token"},
            )
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(self), camera=(), microphone=(), interest-cohort=()"
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(GeometryError)
async def geometry_error_handler(request: Request, exc: GeometryError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"valid": False, "error": exc.as_dict()})


app.include_router(core_router.router, prefix="/api", tags=["core"])
app.include_router(auth_router.router, prefix="/api", tags=["auth"])
app.include_router(farms_router.router, prefix="/api", tags=["farms"])
app.include_router(layers_router.router, prefix="/api", tags=["layers"])
app.include_router(admin_router.router, prefix="/api", tags=["admin"])


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Static PWA (single-origin deployment)
# --------------------------------------------------------------------------- #
DIST = Path(settings.static_dir).resolve()
ASSETS = DIST / "assets"
if ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS)), name="assets")


def _index() -> FileResponse:
    index_path = DIST / "index.html"
    if not index_path.exists():
        return JSONResponse(
            status_code=503,
            content={
                "error": "frontend_not_built",
                "message": (
                    "The web client has not been built. Run 'npm ci && npm run build' in frontend/, or use the "
                    "Docker image which builds it automatically."
                ),
                "api_docs": "/api/docs",
            },
        )
    return FileResponse(index_path, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def root() -> Any:
    return _index()


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> Any:
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    candidate = (DIST / full_path).resolve()
    if DIST in candidate.parents and candidate.is_file():
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if full_path.startswith("assets/") else {}
        return FileResponse(candidate, headers=headers)
    return _index()
