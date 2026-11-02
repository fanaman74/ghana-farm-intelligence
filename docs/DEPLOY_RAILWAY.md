# Deploying to Railway

The app is a single container: FastAPI serves the built React PWA and the JSON API on one port.
That keeps the service worker, CORS and HTTPS on one origin.

## 1. Create the service

Railway → **New Project → Deploy from GitHub repo** (or `railway up` from this directory).

`railway.json` already configures:

```json
{
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": {
    "healthcheckPath": "/api/health",
    "healthcheckTimeout": 180,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5,
    "numReplicas": 1
  }
}
```

The Dockerfile builds the frontend with Node 22, then installs the Python 3.12 backend with
manylinux wheels (rasterio/shapely/pyproj bundle GDAL and PROJ — no system GDAL needed), copies the
built `dist/` to `/app/static`, and starts:

```
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=${GFI_FORWARDED_ALLOW_IPS:-127.0.0.1}
```

Railway injects `$PORT`; the app binds it.

## 2. Add a persistent volume (required for farms)

SQLite and provider caches are written to `GFI_DATA_DIR` (default `/data` in the image).

1. Service → **Variables** → add `GFI_DATA_DIR` = `/data`.
2. Service → **Volumes** → **Add volume** → mount path `/data` (1 GB is plenty to start; the
   SoilGrids VRT metadata is bundled in the image, so the volume only stores SQLite, caches and chips).

Without a volume the app still works, but farms, records and caches are lost on every deploy/restart.

The container starts as root only long enough to take ownership of the mounted volume, then runs the app
as uid 10001 (`gfi`). If your volume is mounted for a different owner, set `GFI_APP_UID` / `GFI_APP_GID`
to match; the entrypoint logs a warning when it cannot chown the directory.

## 3. Environment variables

| Variable | Recommended | Why |
|---|---|---|
| `GFI_ENV` | `production` | enables production logging defaults |
| `GFI_DATA_DIR` | `/data` | persistent volume mount |
| `GFI_ADMIN_TOKEN` | a long random string | protects advisory and market-price writes |
| `GFI_ACCESS_TOKEN` | a long random string | gates farm boundaries, records, analyses and layers behind one shared secret |
| `GFI_FORWARDED_ALLOW_IPS` | your platform's proxy range, or `*` | the container trusts `X-Forwarded-For` only from the loopback by default |
| `GFI_CORS_ORIGINS` | usually unset | only needed if the UI is served from another domain |
| `GFI_JOB_WORKERS` | `3` | parallel analysis jobs; raise carefully on small instances |
| `GFI_ALLOW_DEMO_DATA` | `true` | keeps the clearly labelled sample farm available |
| `GFI_LOG_LEVEL` | `INFO` | `DEBUG` is noisy around provider calls |

Do **not** set `GFI_STATIC_DIR` — the image already points it at `/app/static`.

> **One shared secret, not accounts.** Set `GFI_ACCESS_TOKEN` before you make the URL public: without
> it, every boundary, field, record and analysis is readable and deletable by anyone who finds it.
> With it, the web client asks for the token once and sends `X-Access-Token`; everyone holding the
> token sees the same farms and there is no per-user audit trail. Use an authenticating proxy (Cloudflare
> Access, Railway private networking) if you need separate users. `GET /api/health` reports the posture
> under `auth`.

## 4. Keep one replica

SQLite plus in-process analysis jobs assume a single instance. `railway.json` sets `numReplicas: 1`.
If you need horizontal scale:

1. migrate SQLite → Postgres (the schema is portable; replace `backend/app/db.py` helpers),
2. move jobs from the in-process pool to a queue/worker,
3. then scale replicas and run the API statelessly.

## 5. First-run checks

```bash
curl -fsS https://<your-app>.up.railway.app/api/health
curl -fsS https://<your-app>.up.railway.app/api/catalog/providers | head -c 400
```

Then open the URL on a phone:

1. The onboarding screen appears; choose **Try the sample farm**.
2. The first analysis is cold (~20–40 s) and then cached for 3 hours.
3. Switch to daylight mode and check the layout at 390 px width.
4. Install the PWA (browser menu → *Add to Home screen*); HTTPS on Railway makes the service worker
   and offline caching work.

## 6. What costs what

- **Compute:** analyses are bursty. The first analysis for a farm downloads SoilGrids tiles, CHIRPS
  windows and Sentinel-2 COG windows. Later analyses are usually served from cache (~35 ms).
- **Egress:** the browser downloads ~0.8 MB compressed on first load, then cached by the service worker.
  Chips are ~60–120 KB each and cached.
- **Storage:** SQLite + caches stay in the low tens of MB for hundreds of farms; imagery chips and
  provider caches are the main growth. `provider_cache` can be pruned with
  `python -c "from app.db import cache_prune; print(cache_prune(30))"` inside the service shell.

## 7. Rolling back / redeploying

`docker compose up --build` locally produces the same image. To move data between deployments, copy
the volume's `gfi.sqlite3` (the app uses SQLite WAL; stop the service first) or export each farm as
GeoJSON from the UI and re-import.

## 8. Custom domains and HTTPS

Add the domain in Railway → **Settings → Networking**. Railway provisions TLS; the PWA requires
HTTPS. If you put a CDN in front, do not cache `/api/farms*` or `/api/jobs*`; they are private and
`no-store` is already set.
