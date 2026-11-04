# syntax=docker/dockerfile:1

# ---- 1. Build the React PWA -------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Python runtime ------------------------------------------------------
# rasterio / shapely / pyproj ship manylinux wheels that bundle GDAL and PROJ,
# so no system GDAL installation is required - but the wheel's GDAL still links
# against the *system* libexpat, which python:3.12-slim does not install (CPython
# uses its own bundled copy). Without libexpat1 the container dies at import
# rasterio: "ImportError: libexpat.so.1: cannot open shared object file".
# The base is pinned to a Debian release: the bare -slim tag moved between
# bookworm and trixie and silently changed this dependency set.
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GFI_ENV=production \
    GFI_DATA_DIR=/data \
    GFI_STATIC_DIR=/app/static

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gosu libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user. The entrypoint fixes ownership of the mounted volume
# and then drops to this uid, so the app never runs as root but a root-owned
# volume still works.
RUN groupadd --gid 10001 gfi \
    && useradd --uid 10001 --gid gfi --no-create-home --shell /usr/sbin/nologin gfi

# Install the pinned set, not the ranges: the same image on any build host.
# Regenerate backend/requirements.lock from backend/requirements.txt when updating.
COPY backend/requirements.txt backend/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Fail the build, not the running container, when a wheel's native library is
# missing from the base image. This is the check that would have caught the
# libexpat1 crash-loop above before it reached a deployment.
RUN python -c "import rasterio, shapely, pyproj, numpy, PIL, reportlab; print('native libraries ok')"

COPY backend/ ./
COPY --from=web /web/dist ./static
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# Writable data directory: SQLite, provider cache, imagery chips.
# On Railway mount a persistent volume here so farms and analyses survive deploys.
RUN mkdir -p /data/cache/chips

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=6s --start-period=25s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health" || exit 1

# The entrypoint drops to uid 10001 (see docker/entrypoint.sh).
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Railway provides $PORT. Bind all interfaces so the container is reachable.
# X-Forwarded-For is trusted only from the loopback by default: a wildcard let any
# client spoof its own address in logs and rate accounting. Set
# GFI_FORWARDED_ALLOW_IPS=* when a platform proxy needs to be trusted, or to the
# proxy's address range.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=${GFI_FORWARDED_ALLOW_IPS:-127.0.0.1}"]
