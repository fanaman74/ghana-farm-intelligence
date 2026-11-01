"""Ghana Farm Intelligence — FastAPI application.

Serves the JSON API under /api and the built React PWA from the same origin, so
the app runs on one port locally, on a LAN, or on Railway behind HTTPS.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import admin as admin_router
from .api import core as core_router
from .api import farms as farms_router
from .api import layers as layers_router
from .config import APP_NAME, VERSION, ensure_dirs, settings
from .db import init_db
from .geo.geodesy import GeometryError
from .providers.base import close_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("gfi")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    log.info("Ghana Farm Intelligence %s starting (env=%s, data=%s)", VERSION, settings.env, settings.data_dir)
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
