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
# so no system GDAL installation is required.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GFI_ENV=production \
    GFI_DATA_DIR=/data \
    GFI_STATIC_DIR=/app/static

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=web /web/dist ./static

# Writable data directory: SQLite, provider cache, imagery chips.
# On Railway mount a persistent volume here so farms and analyses survive deploys.
RUN mkdir -p /data/cache/chips && chmod -R 777 /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=6s --start-period=25s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health" || exit 1

# Railway provides $PORT. Bind all interfaces so the container is reachable.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
