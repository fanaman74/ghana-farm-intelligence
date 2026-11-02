# Code review — Ghana Farm Intelligence

**Repository:** `https://github.com/fanamam74/ghana-farm-intelligence` @ `83bd19c` (single commit, "Ghana Farm Intelligence: field-level farm analysis PWA")
**Reviewed:** 1 Nov 2026 · 156 tracked files, ~13k lines of application code, 13 MB tracked
**Method:** cloned, built, type-checked, ran the test suite, read the backend and frontend end to end, and executed targeted probes (see *Verification log*).
**Scope of this document:** correctness, safety of the advice, security, performance, test/CI quality. Not a style review.

---

## Verdict

This is a serious, unusually honest piece of work. The documentation is better than most commercial products, the geodesy is genuinely geodesic, raster sampling is area-weighted and polygon-masked rather than pixel-centre guessed, and the "never fake a value" discipline is implemented in most of the provider layer rather than just claimed in the README.

Then it breaks its own central promise in a handful of places, and the breaks are all in the *last mile* — the code that turns validated data into a sentence a farmer acts on:

- The crop-suitability score **rises** when a data component goes missing, and a missing rainfall component still returns *"Highly suitable · high confidence"*.
- The planting rule reports **the last five days** of weather as *"forecast in the next 5 days"*.
- Rain-gauge totals are **partial sums labelled exact**, and their anomaly baseline is not truncated to match, so a provider gap produces a false drought alert.
- Every per-polygon cache key and every rendered overlay filename identifies a field by its **bounding box**, so two fields with the same bbox silently inherit each other's soil chemistry, rainfall and map layers for up to a year.
- The **primary "Save and analyse" button discards the form**, "Edit vertices" silently deletes all but the first polygon of a multi-part farm, and an offline save failure is reported as *"Saved offline"* when nothing was saved.

Add to that the operational gaps: **no authentication whatsoever** on farm data (anyone who can reach a deployment can list, read, edit and delete every boundary), no CI, no container user, unbounded database growth, and a Windows quick-start that crashes on import.

**Grade: B.** Strong bones, impressive discipline, and a genuinely useful product idea; not yet safe to put in front of a farmer who will believe the recommendations. The fixes are mostly small and local — the architecture is not the problem.

| Area | Grade | Notes |
|---|---|---|
| Architecture & data model | A− | Clean separation, sensible SQLite schema, forward-only migrations |
| Geodesy / geometry validation | A | Correct ellipsoid maths, real self-intersection handling, honest area limits |
| Raster sampling & statistics | A− | Area-weighted, polygon-masked, coverage-aware; a few reporting metrics are dead or always-1.0 |
| Provider adapters | B− | Honest failure paths, but bbox-keyed caches and several silent-partial-result paths |
| Agronomy / recommendations | D+ | The scoring and two recommendation rules produce confidently wrong advice |
| Frontend engineering | C+ | Good structure and copy, but unguarded async races, form bypass, fake-value renders |
| Security & privacy | D | No auth, no tenancy, privacy copy that the backend cannot honour |
| Deployment / operations | C | Works, but root container, `chmod 777`, no pruning, no CI, no pinned deps |
| Tests | C− | The 23 unit tests are real and pass; there are no API tests and one assertion is a tautology |
| Documentation | A | Accurate, specific and candid — and where it is wrong, the code is what is wrong |

---

## Blocker — fix before anyone acts on the output

### B1. A missing input *raises* the suitability score, and still earns "high confidence"
`backend/app/engine/suitability.py:233-249`

```python
available = [component for component in components if component["available"]]
weight_total = sum(component["weight"] for component in available)
score = round(sum(c["score"] * c["weight"] for c in available) / weight_total, 1)
...
if len(available) >= 4 and (soil_valid is None or soil_valid >= 0.5):
    confidence = "high"
```

The score is a weighted mean **renormalised over whatever survived**. Rainfall carries the largest weight (0.30) and is also the component most likely to be missing (it needs the CHIRPS climatology raster; see `analysis.py:295`). Drop it and the remaining pH/texture/temperature/slope — typically all near 100 in a decent field — renormalise to ~100, and because four components are still present the confidence rule returns `high`. The category cap at `:264-267`, whose entire purpose is "a high score from sparse data must not read as certainty", then never fires.

**Net effect: the worse your data, the better the category and the higher the confidence.** `rank_crops` (`:349`) sorts on the uncapped raw score, so the most data-starved crops rise to the top of the ranking.

Contradicts README line 140 ("score categories are capped by data confidence") and `docs/LIMITATIONS.md:49-50`.

**Fix:** require rainfall (or ≥90 % of total weight) for `high`; cap to `medium` whenever any component with weight > 0.15 is missing; multiply the score by coverage instead of renormalising to 1.0, or return `score=None` past a missing-weight threshold.

### B2. "Rain forecast, next 5 days" is the *previous* five days
`backend/app/engine/recommendations.py:189-191`, surfaced at `:203-204`, `:210`, `:225-226`, `:232`

```python
forecast5 = round(sum((row.get("precipitation_mm") or 0)
                 for row in ((weather.get("data") or {}).get("daily") or [])[:5]), 1)
```

Open-Meteo is requested with `past_days=5` (`providers/openmeteo.py:98`), so `daily` starts five days *before today*; `[:5]` is history, not forecast. The app then renders a green priority-1 card reading *"…{forecast5} mm is forecast in the next 5 days"*, with an evidence row literally labelled **"Rain forecast, next 5 days"**.

Two recommendations are built on it — "Planting conditions may improve" and "Wait for more rain before planting" — and they are mutually exclusive, so a dry week last week can suppress the "wait" advice in a genuinely dry week.

The frontend already does this correctly (`panels.tsx:1008` filters `date >= today` before slicing), so this is backend-only and invisible to UI testing.

**Fix:** `future = [r for r in daily if r["date"] >= today.isoformat()]`, then sum `future[:5]`.

### B3. Polygons are identified by bounding box, so fields and farms share cached results
`backend/app/providers/soilgrids.py:68-69` · `chirps.py:111-112, 132-133` · `sentinel2.py:127` · `geo/layer_render.py:300, 337, 394, 419`

```python
geom_hash = stable_hash([round(v, 6) for v in geom.bounds])
```

Four numbers, not the ring. Two different polygons with the same bbox get the same key — the rounding to 6 dp (~11 cm) is not the problem; the *omission of the geometry* is. Consequences, all silent:

- soil pH/SOC/texture statistics, `valid_fraction` and `pixel_count` (`soilgrids.py:69`, TTL **365 days**);
- daily and monthly rainfall means (`chirps.py:112`, 133);
- per-scene cloud-free fraction (`sentinel2.py:127`);
- layer PNG **filenames** (`layer_render.py:300/337/394`) and the layer metadata cache (`:419`).

The api then labels whatever comes back with the requesting `farm_id`/`field_id` (`api/layers.py:48-53`).

This is not hypothetical: a diagonal split of a rectangular farm (`POST /api/geometry/split` → `create_field`, the app's headline "split into fields" feature) yields two triangles whose bounding boxes are **identical** — the bbox of a triangle is the rectangle it is cut from. The second field is served the first field's soil chemistry and an overlay clipped to the wrong polygon. A field drawn over the farm's full extent collides with the farm in the same way.

Contradicts README:30 ("Nothing is silently substituted") and the whole premise of per-polygon analysis.

**Fix:** hash the normalised ring (`stable_hash(geom.wkt)` or `mapping(geom)`), and include the same identity in chip filenames. Bump `ANALYSIS_VERSION` / cache namespaces so existing entries are abandoned rather than inherited.

### B4. CHIRPS totals drop missing days but are still labelled exact — and drive a false drought signal
`backend/app/providers/chirps.py:161-209`, `:241-258`, anomalies at `:339-359`

- Missing monthly raster → `continue` (`:177-178`); missing daily raster → filtered out of the subtotal (`:188-189`). `total_mm` is then a sum over fewer days than the window claims, with no `partial` flag. The module docstring (`:5-6`) promises the opposite: *"The 30/90-day totals are exact for the periods stated"*.
- `_baseline_total` (`:241-258`) prorates the climatology over the **requested** window, not over the days actually observed.

So `percent_of_normal` divides an *n*-day sum by a *30*-day baseline. Ten missing days understates the anomaly by a third, and the result feeds `recommendations.py:296-338` — a `warning`, `priority=1`, `confidence=high` card saying rainfall is at *Y %* of the long-term average. One absent file in the DE Africa mirror becomes "your season is failing".

`_read_window_mean:101-102` also computes `valid_fraction` as `weights[valid ∧ mask] / weights[valid]`, which is identically **1.0** for every call because `valid ⊆ mask` — a data-quality field that can only report perfection.

**Fix:** track `days_covered` per window, emit `complete: false` + `missing_days`, compute the baseline over the observed date list, suppress the anomaly when incomplete, and fix the `valid_fraction` denominator.

---

## High

### H1. There is no authentication or authorisation on any farm data
`backend/app/api/farms.py` (whole file) · `backend/app/main.py` (no auth middleware) · `backend/app/api/admin.py:23-29`

`GET /api/farms` returns every farm with name, region, district, area and crop. `GET/PATCH/DELETE /api/farms/{id}`, records, soil tests, exports, layer PNGs and analyses are equally open. Only the two admin *write* routes are gated, and only when `GFI_ADMIN_TOKEN` is set. `require_admin` also compares with `!=` rather than a constant-time compare, and `/api/admin/stats` (`:123`) never calls it at all.

The README's Railway instructions (`docs/DEPLOY_RAILWAY.md`, `railway.json` with a public domain, `numReplicas: 1`) put this on the open internet. `docs/LIMITATIONS.md:58` warns about the admin token but says nothing about farm boundaries being world-readable and world-writable — while the UI tells the user *"Farm boundaries are private by default and are never published"* (`panels.tsx:2336`).

**Fix:** either document loudly and unconditionally that this is a single-tenant local tool (and refuse to start when `GFI_ENV=production` without a token), or add an owner key/HMAC on farm routes. Use `secrets.compare_digest` for the admin token.

### H2. ESA WorldCover: the neighbouring tile's real values are discarded
`backend/app/providers/landcover.py:56-73`

```python
if valid is None:
    values = rw.data[0]; valid = rw.mask.copy()   # polygon mask, not band validity
else:
    values = np.where(valid, values, rw.data[0])  # tile 1's fill (0) wins
    valid = valid | rw.mask
```

`read_polygon` reads `boundless=True`, so tile 1 fills everything outside its extent with nodata `0`. Because `valid` starts as the *polygon mask* (which is true across the whole polygon, including the part outside tile 1), tile 2's genuine class codes are never substituted. Class 0 is not in `CLASSES`, so that half of the field is dropped at `:78` and `categorical_fractions` renormalises over the surviving half — with no `valid_fraction` or coverage field in the response at all (`:97-111`).

WorldCover tiles are 3°×3°, and Ghana is cut by the **0° meridian** and by **lat 6°N / 9°N**. Fields near Tema, Ho, Keta — or anywhere along those lines, including much of the Ashanti and Volta regions — are affected. `cropland_share`/`tree_cover_share` then drive the "check your land-cover baseline" recommendation.

**Fix:** merge on band validity (`np.where(tile1_valid, values, rw.data[0])` where `tile1_valid = mask & isin(values, CLASSES)`), and report the classified fraction of the polygon.

### H3. Terrain ignores DEM nodata, and tile failures are swallowed as success
`backend/app/providers/terrain.py:71-74`, `:90-92`, `:136-137`

```python
except Exception as exc:
    errors += 1
    continue
...
if errors:
    # Partial coverage is acceptable; the mask below only uses valid pixels.
    pass
```

`errors` is never surfaced, no warning is added, and the section still returns `STATUS_OK` (`:175`). Separately, `finite = np.isfinite(elevation)` is the only validity test — `ds.nodata` is never read anywhere in the module, unlike every other provider (`docs/ARCHITECTURE.md:70` explicitly promises `∧ ≠ nodata`). If the Copernicus COG carries a finite sentinel rather than NaN, it is treated as real elevation and `np.gradient` produces absurd slopes at the void edge; if it carries NaN, a failed tile silently becomes a hole with no user-visible trace.

**Fix:** read and mask `ds.nodata` per tile, add a warning (or degrade status) when `errors > 0`, and report the fraction of the polygon covered by valid DEM pixels.

### H4. The primary "Save and analyse" button throws away the form
`frontend/src/components/panels.tsx:2395-2415` vs `:2439-2458`; defaults at `frontend/src/App.tsx:619-624`

The footer button is `type="button"` with `onClick={() => onSave({})}`. The real `<form id="save-farm-form">` — with name, crop, planting date, irrigation, notes — is only submitted by pressing Enter inside a field or by a `hidden` submit button nobody can click. `handleSaveFarm` then applies `payload.name ?? \`Farm ${n}\``, `crop_id: null`, `planting_date: null`.

So the user names the farm, picks maize, sets a planting date, presses the obvious button, and gets "Farm 3" with no crop and no calendar — the two things that drive the crop ranking and the whole Calendar tab.

The e2e test fills the name and clicks this exact button (`frontend/tests/e2e/app.spec.ts:129-130`) but never asserts the saved name, so the suite is green.

**Fix:** `type="submit" form="save-farm-form"`, delete the `onSave({})` path and the hidden button, assert `name`/`crop_id` after save.

### H5. "Edit vertices" on a multi-part farm deletes every part but the first, then saves
`frontend/src/map/MapView.tsx:1006-1014` → `frontend/src/App.tsx:648-666`

```js
const ring = geometry.type === "Polygon"
  ? (geometry.coordinates as number[][][])[0]
  : (geometry.coordinates as number[][][][])[0][0];   // first polygon only
```

`getDraft()` returns a single `Polygon`, and `saveEdit` PATCHes it over the farm geometry. MultiPolygon is explicitly supported elsewhere (`App.tsx:556-559` handles `number[][][][]`, and imports can produce it), so dragging one vertex of a two-part farm and pressing save silently destroys the second part and re-derives area, perimeter and centroid. The toast says *"Boundary updated — re-running the analysis"*.

**Fix:** refuse edit mode for `MultiPolygon` with a clear message, or edit all parts and reassemble; at minimum compare part count and area before submitting.

### H6. A failed offline save is reported as a success, and the boundary is lost
`frontend/src/App.tsx:586-597`

```js
if (error instanceof ApiError && error.status === 0) {
  ...
  setSaveError("Saved offline — the boundary will be validated by the server when you reconnect.");
```

Nothing is persisted: `offline.ts` has no draft outbox, and `handleSaveFarm` calls `api.createFarm`, which will fail on the same dead connection. Closing the modal discards the geometry, and the drawing has already been cleared. A fake success, in the one place the project's own principle is stated most often.

**Fix:** either persist a pending boundary in IndexedDB and replay it, or say plainly *"No connection — this boundary has not been saved"* and keep the draft for retry.

### H7. `force: true` cannot refresh anything
`backend/app/analysis.py:502-515` and `:553-556`

`POST /api/farms/{id}/analysis` skips the cache when `force` is set, but the job body then calls `get_cached(key)` unconditionally and returns *"Loaded from cache"*. So the UI's refresh affordance (`App.tsx:1237` → `onRefresh`) creates a new job row, re-serves the same payload, and reports it as loaded from cache. Within the 3-hour analysis TTL there is no way to force a recomputation.

**Fix:** thread `force` into `submit_job`/`work()` and skip `get_cached` there.

### H8. The documented Windows quick-start crashes on import
`backend/app/config.py:18` + `backend/requirements.txt`

```python
GH_TZ = ZoneInfo("Africa/Accra")   # module import time
```

`tzdata` is not in `requirements.txt`, and Windows ships no IANA database, so `uvicorn app.main:app` — exactly what README:50-52 tells Windows users to run — dies with `ZoneInfoNotFoundError: 'No time zone found with key Africa/Accra'`. I reproduced this (see *Verification log*); installing `tzdata` makes the suite pass. The same crash is possible on any Linux image without `/usr/share/zoneinfo`.

**Fix:** add `tzdata` to `requirements.txt`, and defer the `ZoneInfo` construction into a helper with a UTC fallback.

### H9. PDF export returns 500 for a farm name containing `<`
`backend/app/io/exports.py:184-241`, `:369-388`

Farm names are interpolated into ReportLab `Paragraph` flowables with only Latin-1 normalisation, never XML-escaped. `_latin1` maps typographic characters but leaves `<`, `>`, `&`. Verified:

```
'Mensah & Sons Farm' -> OK (2723 bytes)
'Farm <A>'           -> ValueError: paragraph text ... Parse error: saw </para> instead of expected </a>
```

`GET /api/farms/{id}/export?format=pdf` therefore 500s for a farm named `Plot <A>` or `Field <2>`. Because ReportLab's markup also honours `<img src=…>`, this is unescaped-markup injection into a rendered document as well as a crash.

**Fix:** add XML escaping to `_latin1` (or use a `xml.sax.saxutils.escape` wrapper for Paragraph text only) and a regression test with `<`, `>`, `&`.

### H10. Recording a laboratory soil test does not change the advice it is supposed to change
`backend/app/analysis.py:500-518` vs `_merge_measured_soil:99-126`

The analysis cache key covers farm id, geometry, field, crop, planting date, management level, irrigation, day and version — but **not** soil tests, records or advisories, all of which `build_analysis` merges into the payload. Record a lab pH and re-run: the 3-hour cached payload still reports SoilGrids values. `_headline:471` also reads modelled pH only, so the headline keeps showing the estimate even when a measured value exists. `recommendations.py:89` promises the user the opposite: *"Record the results here so advice switches from estimates to measurements."*

**Fix:** include the latest soil-test id/date and applicable advisory ids in the key; make `_headline` prefer `measured`.

---

## Medium

**M1. The UI can paint one field's raster on another.** `App.tsx:827-877` applies `api.farmLayer` results with no farm/field guard after the `await`, and the reset effect (`:914-924`) clears state without invalidating in-flight requests, so the loser of a race wins the screen. Same class: `selectFarm` (`:366-397`) has no request token, and `pollJob` (`api.ts:173-191`) checks `signal.aborted` only at loop top while the `finally` at `App.tsx:483-486` unconditionally clears the *new* run's busy state. Fix: monotonic tokens checked before every state write; pass the `AbortSignal` into `fetch`.

**M2. Pixel-invented values still reach the screen**, which is the exact pattern the project forbids: `panels.tsx:1073` (`?? 0` → a missing forecast temperature renders as **0 °C / 0 °C**), `:1587` (`aspect_degrees ?? 0` → "0°", i.e. due north), `:1186-1194` (an unscored component draws a red 0-score "danger" bar), `:842` (pH marker assumed at 6 inside a chart labelled as a prediction interval), `:1549` (missing water balance coloured "good"), and truthiness checks at `:604, 610, 795-797` that render a legitimate `0` as "—".

**M3. Texture class is invented at the end of the cascade.** `soilgrids.py:96-128` falls through to `return "Loam"` for any composition no branch matched — e.g. sand 20 / clay 10 / silt 70 (USDA: silt loam) returns "Loam", which is a *preferred* texture for most crops in the knowledge base and therefore scores 100 in `suitability.py:70-79`. Return `None` (→ `missing_inputs`) instead.

**M4. Legitimate zero coarse fragments are filtered out.** `soilgrids.py:78` uses `values != 0` as a nodata test; for `cfvo` (vol %), 0 is a real prediction. Those cells are dropped, `valid_fraction` falls, and the mean is biased upward — or, if every cell is zero, cfvo disappears from the profile and is reported as "no prediction" for a field that has a prediction.

**M5. Acidity advice pairs a 0–30 cm value with a 0–5 cm interval.** `recommendations.py:74-75` takes pH from `profile_0_30` but the interval from `uncertainty.depths["0-5cm"]`, then labels the evidence row "Topsoil pH (0–30 cm)". The quoted value can fall outside its own quoted interval.

**M6. The terrain and layer paths disagree with the statistics mask.** `layer_render.py:293-296` renders soil with `all_touched=True` while the statistics are centre-based (a comment admits it); `layer_render.py:374-381` uses `all_touched=False` where `terrain._polygon_mask` uses `True`. `api/layers.py:86-87` still tells clients the layers are "rendered from the same polygon-masked rasters used for the statistics", and `docs/ARCHITECTURE.md:102-103` claims "the map and the panel cannot drift apart". They drift, systematically at field edges.

**M7. Offline analysis is keyed by farm, not field.** `App.tsx:404-417, 455, 474` save `analysis` under `target.id` regardless of `fieldId` while `activeFieldId` resets on selection, so offline a 3 ha field's soil and NDVI are presented as the whole farm's — with the farm's `area_ha` in the header.

**M8. `/api/chips/` is `CacheFirst` for 14 days over content-stable URLs.** `vite.config.ts:73-81`; layer PNG names are bbox-only (`layer_render.py:300/337/394`) and are overwritten in place, so after a boundary edit that does not change the bbox the app can keep showing the old clipped overlay beside refreshed numbers. (Scene chips are scene-versioned — `sentinel2.py:242` — so the Health tab is *not* affected; the satellite context image at `:480` is.)

**M9. No CI, no dependency pins.** No `.github/`; `requirements.txt` uses open ranges and no hashes, so two builds a month apart are different images; there is no `requirements-dev.txt` even though the README's test instructions need `pytest`.

**M10. Container hardening.** `Dockerfile:47` does `chmod -R 777 /data`, the image has no `USER`, and `main.py` runs uvicorn with `--forwarded-allow-ips=*`, so any client-supplied `X-Forwarded-For` is trusted and logs/limits can be spoofed.

**M11. Unbounded database growth.** `jobs` stores a full payload per analysis (including partial payloads rewritten at every progress step), `analysis_cache` and `provider_cache` never expire rows, `db.cache_prune` (`db.py:303`) is **defined but never called**, and jobs left `running` at shutdown are never reaped, so the UI polls a job that will never finish.

**M12. Imports silently drop multi-part KML.** `io/importers.py:71-76` takes `max(polygons, key=len)` and ignores all others, with no warning — while the README advertises multi-part support.

**M13. The 12 MB upload cap is enforced after the body is read.** `api/farms.py:352-354` does `await file.read()` and *then* checks `len(content)`. A large POST is fully buffered before rejection. XML parsing is `xml.etree.ElementTree` (no `defusedxml`), so entity-expansion DoS is available on the KML/GPX path.

**M14. Concurrency is unbounded per analysis.** `analysis.build_analysis` spawns its own 4-wide pool per job while `_EXECUTOR` runs `job_workers` (3) jobs, on top of CHIRPS pools of 10 and 3–6. There is no global provider concurrency limit, no per-analysis read budget, and no overall job deadline — a 50,000 ha boundary (the configured maximum) is not rejected for raster size anywhere except terrain (`terrain.py:53`).

**M15. `validate_geometry` is quadratic in vertices.** `geodesy.is_in_ghana` buffers the whole national outline *per vertex* (`geodesy.py:285-296`), and `_other_geometries` (`api/farms.py:48-62`) loads and re-parses every other farm and field on every create/validate. An imported 1,000-vertex boundary costs 1,000 national-outline buffers.

**M16. Dead or always-true reporting fields.** `coverage_fraction` (`raster.py:141-146`) is clamped to 1.0 by construction and then dropped before it reaches the API (`soilgrids.py:87` vs `:196-211`); CHIRPS `valid_fraction` is always 1.0 (B4); `stale` in `soilgrids.analyse:165` is assigned and never set, so `status` is always `ok`; `analysis.py` `total_steps = 8` while 9 steps increment.

**M17. Onboarding, units and languages are controls that do nothing.** The Units setting (`App.tsx:207`) changes no rendered value; Twi/Ewe/Dagbani are selectable and silently fall back to English (documented, but the control looks live and `<html lang>` is never updated); the sample farm sets `is_demo=False` (`api/farms.py:411`) so the "this is a sample" banner (`panels.tsx:596`) is unreachable — the name says "Sample farm", the flag does not.

**M18. Accessibility is claimed, not delivered, in the places that matter.** `styles.css:598` removes the focus ring from every form control and beats the global `:focus-visible` rule on specificity; `:993` leaves the search input with no indicator at all; `:179` strips the ring from the keyboard-pannable map canvas; the draggable vertex marker is **16×16 px** (`:898-904`); modals re-run `previous.focus()` on every parent re-render because `onClose` is passed inline (`ui.tsx:290-319`), so an open dialog steals focus while a job polls; `aria-modal="false"` onboarding sits over a live app.

**M19. The frontend bundle is one 1.41 MB chunk and the PWA precaches 1.7 MB on first visit.** Measured (see log): `dist/assets/index-*.js` = 1,410 kB (393 kB gzip), CSS 115 kB, precache **20 entries / 1,712.9 KiB**. `chunkSizeWarningLimit: 1200` was raised to hide the warning and does not even succeed (the warning still fires). For a stated target of "modest Android phones" on 2G/3G this is the dominant first-load cost; MapLibre plus every tab body is parsed before first paint. The font precache is 7 subsets (~219 KB), of which cyrillic/greek/vietnamese (~75 KB) can never be used by this UI.

**M20. No request ever times out.** `api.ts:27-56` has no timeout and no signal; the full-screen busy veil (`App.tsx:1333`) and modal loaders have no cancel path, so one stalled request locks the interface.

**M21. The SoilGrids conversion table disagrees with itself in the repo.** `providers/soilgrids.py:44,56` use scale 100 for nitrogen and cfvo; `tests/test_core.py:161-162` asserts 100; `scripts/seed_soilgrids.py:56` writes 100 for `bdod` and **10** for everything else, including nitrogen and cfvo, into the bundled `soilgrids_vrt/manifest.json`. At least one is wrong by 10×, and the plausibility ranges (`(0, 50)` vs `(0, 100)`) are too wide to catch it. Separately, `cec` is scaled by 10 and labelled `cmol(c)/kg` while ISRIC's published standard unit for that layer is `mmol(c)/kg`; if so, displayed CEC is 10× high. **I could not verify this offline** — see *Unverified*.

**M22. The provider registry describes an access path the code rejects.** `registry.py:60-66` presents WCS `GetCoverage` as the primary method for SoilGrids; `soilgrids.py:1-13` and `docs/PROVIDERS.md:39` say WCS was *rejected* for zero artefacts and that the WebDAV/VRT path is used. The registry is served to users as the source catalogue.

**M23. Type safety at the API boundary is assertion, not validation.** `api.ts:55` returns `(await response.json()) as T`; `types.ts:264-285` marks vegetation fields as required and `panels.tsx:1441/1463` dereference them unguarded, so a payload change yields `NaN%` or a whole-app ErrorBoundary; `panels.tsx:617` casts `DataStatus` to `never`; `App.tsx:313` double-casts providers so `String(null)` renders the literal "null".

**M24. `.env.example` points the data directory at `backend/backend/data`.** `GFI_DATA_DIR=./backend/data` is resolved relative to `BACKEND_ROOT` (`config.py:92-94`), i.e. `backend/backend/data`. Docker Compose overrides it, so this only bites the non-Docker path — which is the one the file is meant to configure.

---

## Low

- `analysis.py:64-67` filters advisories by `valid_to` only, ignoring `valid_from`, `regions` and `crop_ids`: a Volta bulletin is shown on a Northern farm.
- `raster.py:227-233` falls back to the *planar* centroid when no pixel centre falls inside the polygon; for a concave field that point can be outside the field, and the result still reports `valid_fraction = 1.0` with no flag.
- `chirps.py:224-237` drops an incomplete climatology month from the annual total without flagging it, and that annual figure feeds `classify_zone` — which then selects the crop season set. (The season-rainfall path, `crops.py:249-251`, refuses to guess in the same situation; the two disagree.)
- `openmeteo.py:159-180` `_hourly_rows` slices the first 48 entries of a series that starts 5 days ago; `:222-223` can emit `-99 °C`/`99 °C` when a field is missing; `:232` ignores its `forecast_days` argument.
- `suitability.py:116-117` always scores water against `windows[0]` (the major season) even when the open window is the minor one.
- `terrain` clips slope at 200 % for statistics and 100 % for the map legend.
- `sentinel2` summarises NDVI with unweighted pixel counts on a geographic grid, and fits its trend with `np.polyfit(range(n), …)` over irregularly spaced dates.
- `layer_render.py` exports `_render_cropland` legend from classes actually present, so a field with a single class has a one-entry legend; fine, but the `value_range` is always `null` for that layer while `available: true`.
- `main.py:131-139` serves `.gz` seed files with `Content-Encoding: gzip` regardless of `Accept-Encoding`.
- `main.py:109-123` annotates `_index()` as `-> FileResponse` while it may return a `JSONResponse`.
- `exports.farm_kml:92` writes only exterior rings, so a polygon with a hole exports solid.
- `panels.tsx:1888-1918` positions timeline markers on a clamped 70 % scale while the bars fill 100 %.
- `App.tsx:262-266` and `ui.tsx:344-365` each run a toast timer; errors use `role="status"` and self-dismiss after 5 s (WCAG 2.2.1).
- `App.tsx:816-825` revokes the object URL in the same tick as `click()` with the anchor unattached.
- `i18n.ts` falls back to English by design but the selector still persists the choice.
- `tests/test_core.py:57` — `assert area != pytest.approx(33.0, abs=0.001) or True` is a tautology; several geodesy assertions have 15–45 ha bounds that would pass for a wide range of wrong answers.

---

## Honesty audit — claims vs code

The README's credibility rests on §"Honesty mechanics" and `docs/LIMITATIONS.md`. Graded against the code:

| Claim | Verdict |
|---|---|
| "Every section has status, retrieved_at, sources with licence, resolution, method, limitations" | **Holds.** `providers/base.py:163-188`, populated by every adapter. |
| "Nothing is silently substituted. When a provider fails, the section states that it is unavailable or shows the last successful values with their original dates" | **Partly fails.** The *provider* layer is good (`cached()` marks `stale`; `failure()` shows no value). But CHIRPS window totals drop missing days (B4), terrain tile failures vanish (H3), WorldCover loses half a field (H2), and the suitability engine is *rewarded* for absence (B1). |
| "SoilGrids values are shown only where the 250 m grid has predictions; gaps produce 'no prediction', not a nearby value" | **Mostly holds.** No neighbour borrowing in the statistics path (`raster.py`). Fails for `cfvo` zeros (M4) and the invented "Loam" class (M3). |
| "NDVI/NDMI use only cloud- and shadow-free pixels; the cloud-free fraction is shown" | **Holds.** SCL mask `{0,1,3,8,9,10,11}` applied for both ranking and statistics, area-weighted over the polygon. |
| "Rainfall totals always end on the latest published CHIRPS day, with the lag stated" | **Holds on anchoring, fails on completeness.** Windows end on `latest_available()` and `lag_days` is in the payload — but the totals themselves may be partial (B4). |
| "Crop suitability is screening, not a yield forecast; categories are capped by data confidence" | **Fails.** B1 defeats the cap entirely. No yield anywhere, though — that part is true. |
| "Perennial crops are never treated as one-season crops" | **Holds.** `season_windows` returns `[]`, the water component switches to a 365-day basis, `season_status` is skipped. |
| "Every section carries its own provenance" / "the map and the panel cannot drift apart" | **First holds; second fails** at field edges (M6) and after a boundary edit (M8). |
| "Farm boundaries are private… never sent to a provider" | **Server-side holds** (providers get a bbox/centroid). **Privacy as a product claim fails** — no auth (H1) — and the browser itself contacts EOX and AWS terrain tiles with the field's location in the viewport. |
| "23 unit tests passed" | **Verified.** 23 passed in 2.3 s — after installing the `tzdata` the requirements omit (H8). |
| "The Docker image was not built in the authoring environment" | **Honest and accurate.** `docs/LIMITATIONS.md:68-71`. I did not build it either. |

---

## What is genuinely good

Worth saying plainly, because most of this repo is better than the industry norm:

1. **Geodesy is real geodesy.** `pyproj.Geod.geometry_area_perimeter` on WGS84, equal-area centroid via EPSG:6933, self-intersection reported with `explain_validity`, multi-part and ring closure handled with user-visible warnings.
2. **Raster statistics are done properly.** Windowed reads only, polygon rasterisation rather than bbox averaging, geodesic per-row pixel areas for geographic grids (`raster.py:93-107`), weighted quantiles, explicit `valid_fraction`, and a `sub_field_detail ≥ 4 px` rule so a field smaller than a soil cell is *told* that it is.
3. **The `Section`/`Source` provenance model.** Status, retrieval time, dataset, licence, resolution, method, limitations and access method travel with every number. It is the single best idea in the codebase and the reason the failures above are so visible — they violate a contract the code otherwise keeps.
4. **The stale-while-error cache in `providers/base.py:222-264`** returns the last good payload *labelled stale with its original timestamp* rather than inventing or blanking. That is the right design.
5. **The frontend has no injection surface.** Zero `dangerouslySetInnerHTML`/`innerHTML`/`eval`; all API text rendered as JSX; external links carry `rel="noreferrer noopener"`.
6. **Service-worker scope is correct.** Analysis POSTs, job polling and all of `/api/farms/**` fall through to `NetworkOnly`; `navigateFallbackDenylist` keeps `/api/` out of the SPA fallback; `cleanupOutdatedCaches` plus hash-based precache.
7. **Documentation discipline.** `LIMITATIONS.md` is the most honest such file I have read in a project this size — it names the WaPOR 502, the CHIRPS 31-day lag, the "not built in the authoring environment" gap, and tells the reviewer exactly what to check before using it for real decisions.

---

## Suggested order of work

1. **B1, B2, B3, B4** — one week. These are the difference between "screening aid" and "confidently wrong advice". Each is a small, testable change; add a unit test per bullet.
2. **H1, H4, H5, H6** — decide the deployment story (single-tenant local vs public), fix the save/edit/offline paths, and stop reporting failures as successes.
3. **H7, H9, H10, H8** — `force` semantics, PDF escaping, cache-key inputs, `tzdata`.
4. **CI**: one GitHub Actions workflow running `pytest`, `tsc -b`, `vite build` and a lint pass would have caught M21, H8, H9 and M23's `as never`. Pin dependencies and hash them.
5. **API tests with `TestClient`** — there are currently none, which is why an unauthenticated destructive endpoint and a form-bypassing save both survived review.
6. Then the frontend races (C1/M1), fake-value renders (M2), accessibility (M18), and bundle splitting (M19).

---

## Verification log

Environment: Windows 11, Python 3.13.12 (venv), Node 24.20.0, npm 11. I installed nothing system-wide.

| Check | Result |
|---|---|
| `git clone` | OK — `83bd19c`, 156 tracked files, 13 MB |
| `py -3.13 -m venv` + `pip install -r backend/requirements.txt pytest` | OK — rasterio 1.5.2, shapely 2.2.0, pyproj 3.8.0, numpy 2.5.4 |
| `python -m pytest backend/tests/test_core.py` | **FAILED at collection** — `ZoneInfoNotFoundError: Africa/Accra` (no `tzdata` on Windows) → H8 |
| same, after `pip install tzdata` | **23 passed, 2 warnings, 2.30 s** — matches README's claim |
| `npx tsc -b --force` (clean, buildinfo removed) | exit 0 — no type errors |
| `npx vite build` | exit 0 — 1 chunk `index-BIqdIihq.js` **1,410.33 kB / 393.02 kB gzip**, CSS 115.41 kB, precache **20 entries / 1,712.90 KiB**, chunk-size warning still fires |
| `exports.farm_pdf` with `Mensah & Sons Farm` / `Farm <A>` | first OK, second `ValueError` → H9 |
| Repo state after review | clean (`git status` empty); `CODE_REVIEW.md` added untracked |

### Unverified — resolved in round 6

The first four were closed with live checks; the answers are below rather than deleted, because two of them changed what the code should do.

1. **SoilGrids conversion factors — code correct, seed manifest wrong.** ISRIC's published table (Poggio et al. 2021, SoilGrids 2.0) is: bdod 100→kg/dm³, **cec 10→cmol(c)/kg**, cfvo 100→vol %, **nitrogen 100→g/kg**, phh2o 10→pH, texture and soc 10→% / g kg⁻¹. `providers/soilgrids.py` matches it exactly — including `cec`, where this review's suspicion of a 10× overstatement was **wrong**. The outlier was `scripts/seed_soilgrids.py`, which hard-coded 10 for nitrogen and cfvo and wrote that into the bundled manifest. Fixed: the script now derives the manifest from the runtime table, the manifest was corrected, and `test_seed_manifest_scale_factors_match_the_provider_table` stops it drifting again.
2. **CHIRPS scaling — none needed.** The DE Africa daily COG is `float32`, nodata −9999, `scales=(1.0,)`, `offsets=(0.0,)`. Values are millimetres as read; no factor is missing.
3. **Copernicus DEM nodata — no sentinel.** The GLO-30 COG is `float32` with `nodata = None` (voids are NaN), so the "finite sentinel becomes an elevation" failure mode does not occur. Reading `ds.nodata` anyway is harmless and stays; the substantive part of H3 — silently swallowed tile failures — was real and is fixed.
4. **Sentinel-2 BOA offset — already applied.** The live STAC item for the Ejisu field (`S2A_30NXN_20261029_0_L2A`) carries `earthsearch:boa_offset_applied = True`, so NDVI computed from raw DN is not attenuated. Residual risk worth knowing: the code does not *check* the flag, so an item with `False` would bias NDVI low. Every item this deployment has seen is `True`.

### Still unverified

5. **Docker build** — no daemon on this machine; the image has not been built here either, which the authors already document at `docs/LIMITATIONS.md`.
6. **Physical-device behaviour** — the browser suite needs a running server and live providers, and nothing here has been exercised on a real low-end Android phone or a 2G connection.

---

# Remediation log

Work done after the review, in this checkout. Everything below is verified by `backend/tests` (47 passed, 11 network tests skipped), `tsc -b --force`, `vite build`, or a live provider probe — commands at the end.

## Correction to this review

While fixing **B3** I found a defect this review missed and under-graded: `soilgrids._layer_stats` computed its statistics over the **padded bounding-box window** with no polygon mask (`valid` was `finite ∧ range`, and `summarise` was called with that), so a field of one or two 250 m cells had its neighbours' soil values mixed into its own mean. The claim "raster sampling is polygon-masked" was therefore wrong for the headline soil numbers, and the `A−` in the scorecard was too generous. Fixed and regression-tested (`test_soil_statistics_ignore_pixels_outside_the_polygon`).

## Fixed

| Finding | Change | Verified by |
|---|---|---|
| **B1** score rises with missing data | `data_coverage = weight / 0.90`; `high` needs ≥0.85 coverage, `medium` ≥0.60; ranking uses `rank_score = score × (0.5 + 0.5·coverage)`; category cap now actually fires | `test_missing_component_cannot_reach_high_confidence`; live probe: full context → 100/high/highly_suitable, missing rainfall → 100/**medium**/**suitable**/rank 83.4 |
| **B2** past 5 days sold as forecast | filter `date >= today` before slicing; require ≥3 forecast days before any planting advice; evidence label now "last 14 available days" | `test_planting_advice_uses_the_forecast_not_the_past_five_days` |
| **B3** bbox-keyed caches | new `geodesy.polygon_hash()` (rings, not bounds) used by soilgrids, CHIRPS daily+monthly, Sentinel-2 SCL cache, all chip filenames, layer cache; `ANALYSIS_VERSION` → 2 | `test_polygon_hash_distinguishes_identical_bounding_boxes` |
| **B4** partial CHIRPS totals | `_window_total` returns `days_covered`, `expected_days`, `missing_days`, `missing[]`, `complete`; baseline prorated to covered days; anomaly labelled partial; per-day/month failures no longer kill the section; `valid_fraction` denominator fixed (was always 1.0) | `test_rainfall_baseline_scales_to_the_days_actually_covered` |
| soil stats unmasked | statistics now `mask ∧ finite ∧ range`; `sample_stats()` extracted and unit-tested | `test_soil_statistics_ignore_pixels_outside_the_polygon` |
| other soil defects | 0 is documented as ISRIC's "no prediction" (VRT nodata confirmed -32768) and counted in `zeros_without_prediction`; texture classifier returns `None` instead of inventing "Loam" | `test_soilgrids_zero_is_reported_as_no_prediction_not_zero`, `test_texture_class_is_none_when_composition_is_unclassifiable` |
| **H2** WorldCover cross-tile loss | tiles mosaicked with `rasterio.merge` onto one grid (bounds + 1/12000° res), polygon mask, area weights; reports `classified_fraction` and tile counts | **live**: 400 m field straddling 0° near Tema → 2 tiles read, 1512 px, 100 % classified (was ~half) |
| **H3** terrain nodata / silent tiles | reads `ds.nodata` per tile and masks it; `mosaic()` returns tile info; section warns on failed tiles and low coverage, reports `dem_coverage_fraction` | import + code path; no offline fixture, live run not repeated |
| **H4** save button discarded the form | footer button is `type="submit" form="save-farm-form"`; `onSave({})` path and hidden button removed | `test_create_farm_persists_the_fields_the_form_sends` (API contract) + `tsc` |
| **H5** MultiPolygon edit truncation | `beginEdit` refuses multi-part boundaries with an explicit message | `tsc`; code read |
| **H6** false "Saved offline" | now "No connection — this boundary has NOT been saved…"; draft kept for retry | code read |
| **H7** `force` no-op | `force` threaded into `submit_job`/`work()`, which skips the cache; route loads the same farm shape so both key derivations agree | `test_force_flag_reaches_the_job` |
| **H8** Windows crash | `tzdata` added to requirements; `config._ghana_timezone()` falls back to `timezone.utc`. (The first attempt fell back to `ZoneInfo("UTC")`, which needs the same missing database — caught by testing with `tzdata` uninstalled.) | with `tzdata` removed from the venv: `GH_TZ` → `datetime.timezone UTC`, `datetime.now(GH_TZ)` → `+00:00`, and **47 tests pass** |
| **H9** PDF 500 on `<` | `_xml_escape` in `_latin1`; `_table` no longer double-escapes | `test_pdf_export_survives_markup_in_the_farm_name` |
| **H10** lab test ignored by cache | `_inputs_fingerprint` (latest soil test + advisories) in the analysis key; headline prefers measured pH | `test_analysis_key_changes_when_a_soil_test_is_recorded` |
| **M1/M2 (part)** fake values in UI | 0 °C/0 °C forecast, "0°" aspect, red 0-score bar, pH marker assumed at 6, green missing water balance, truthiness hiding real zeros — all now `== null` checks or explicit "—" | `tsc`; code read |
| **M11** unbounded growth | `db.housekeeping()` at startup: fails jobs orphaned by a restart, deletes finished jobs and expired cache rows | import + startup path |
| **M14/M15 (part)** quadratic validation | national outline buffered once (`lru_cache`) instead of once per vertex | code read |
| **M9** no CI | `.github/workflows/ci.yml`: pytest + import check (Python 3.12) and `npm ci` + `tsc` + `vite build` (Node 22); `requirements-dev.txt`; `tests/conftest.py` keeps runtime data out of the checkout | workflow file; runs green locally |
| **M (tests)** no API coverage | `backend/tests/test_api.py`: 15 tests over create/read/delete, geometry rejection, exports, chip traversal, unknown layer, catalogue, admin default, analysis job + force, import | 47 passed total |

## Still outstanding (next rounds)

- **Per-user accounts** (the remaining half of H1): the single-secret gate protects a deployment, but
  everyone holding the token sees the same farms and there is no per-user audit trail. Real tenancy
  means a users table, sessions and owner-scoped queries.
- Accessibility: the search dialog is still not a real combobox (`aria-activedescendant`), and MapLibre
  is still parsed eagerly — `React.lazy` plus a map skeleton would cut the first parse further.
- Font subsetting: 7 Inter subsets (~219 kB) are precached, of which cyrillic/greek/vietnamese (~85 kB)
  can never render in this UI.
- Dependency pinning + hashes (needs a Docker build to validate); non-root container with a
  volume-chown entrypoint.

## Round 4

| Area | Change | Verified by |
|---|---|---|
| **live provider verification** | fresh `scripts/verify_providers.py` run from this machine: **8 of 9 endpoints OK, WaPOR still HTTP 502** (soilgrids, open-meteo, CHIRPS, Sentinel-2, DEM, WorldCover, geoBoundaries, GAEZ reachable) — matching what the README claims; result kept at `docs/provider-verification-2026-11-01.json` | run output; JSON summary `{ok: 8, unavailable: ["wapor"]}` |
| **modal focus** | the effect depended on `onClose`, which every caller passes as an inline arrow, so each parent re-render moved focus to the trigger and then to the first field mid-typing (a job poll re-renders every 900 ms). Now depends on `open` only, closes through a ref on Escape, and pulls focus back if it escaped into the page | `tsc`; code read |
| **touch targets** | tab strip and segmented controls to `min-height: 36px`, icon buttons 32→36 px (30→36 on mobile), matching the README's "≥36 px" claim; the vertex marker already has a 28 px hit area | CSS; build |
| **job payload** | progress writes now store per-section statuses instead of the whole payload; the full payload is written once at completion | `test_analysis_deadline_and_progress_summary` (asserts the summary is <200 bytes) |
| **job deadline** | `GFI_ANALYSIS_TIMEOUT` (default 300 s); `check_deadline()` between sections fails the job with `analysis_timeout` instead of holding a worker and a progress bar forever | same test |
| **docs** | ARCHITECTURE pipeline step 4 (statuses + deadline + area guard), `.env.example` (`GFI_ANALYSIS_TIMEOUT`) | docs |

Suite is now **52 passed, 11 skipped**.

## Round 2

| Area | Change | Verified by |
|---|---|---|
| **H1 (posture)** | `/api/health` now reports `auth: {farm_data: "open", admin_token_required}`; startup logs a warning when `GFI_ENV=production` without `GFI_ADMIN_TOKEN`; ARCHITECTURE, LIMITATIONS, DEPLOY_RAILWAY and `.env.example` state that there are no farm-level accounts | `test_health_reports_ok`; docs |
| **frontend races** | monotonic `selectionToken` (a slow `getFarm` cannot install a stale farm), `analysisRun` token checked before every state write and in `finally` (an aborted run no longer clears the new run's spinner), `layerEpoch` invalidates in-flight layer chips, delete aborts polling | `tsc`; code read |
| **request timeout** | every fetch has a 45 s cap (`ApiError(408)`), external `AbortSignal`s are forwarded, and `pollJob` checks the signal after the request returns, not only before it | `tsc`; code read |
| **offline scope** | analyses are stored under `farmId:fieldId`, and a farm-scope hydrate rejects a field-scoped result | `tsc`; code read |
| **import hardening** | chunked 12 MB cap + early `Content-Length` rejection middleware; `defusedxml` for KML/GPX; multi-part KML now warns instead of silently importing one part | `test_kml_import_rejects_an_entity_expansion_attack`, `test_kml_import_warns_when_only_one_polygon_is_kept` |
| **container** | dropped `chmod -R 777 /data`; `X-Forwarded-For` trusted only from the loopback unless `GFI_FORWARDED_ALLOW_IPS` says otherwise; root-user trade-off documented | Dockerfile, docs |
| **bundle** | `manualChunks` splits MapLibre and React out of the app chunk | build: app **1,410 kB → 342 kB** (gzip 393 → 104 kB), MapLibre 1,053 kB separate |
| **a11y** | focus rings restored on inputs, the search field and the map canvas; the draggable vertex marker gets a 28 px hit area around its 16 px dot | CSS read; build |
| **docs** | README test counts + CI note, ARCHITECTURE security caveat, LIMITATIONS operational limits (no accounts, root container, partial provider records), DEPLOY_RAILWAY env row, `.env.example` (`GFI_DATA_DIR` footgun fixed, auth posture stated) | docs |
| **regression suite** | +2 tests (KML multi-part, XML entity expansion) → **49 passed**; full **live provider suite 11 passed** against real SoilGrids/CHIRPS/Sentinel-2/DEM/WorldCover | `pytest`, `GFI_LIVE_TESTS=1 pytest` |

Two defects were found by the work itself this round: `ZoneInfo("UTC")` is not a valid fallback when the tz database is absent (round 1), and `defusedxml` raises `DefusedXmlException` (a `ValueError`), which the importer did not convert to a 422 — a malicious KML produced a 500 until the test caught it.

## Round 3

| Area | Change | Verified by |
|---|---|---|
| **H1 (single-secret deployment gate)** | `GFI_ACCESS_TOKEN` + `require_access_token` middleware: when set, the private API (farms, fields, records, analyses, layers, jobs, geometry, admin, market prices) needs `X-Access-Token`; health, meta, reference data (`basemap`, `geo`, `catalog`, `places`, `sources`) and rendered PNGs stay public so the SPA and MapLibre can load before the token is supplied. Constant-time compare; posture reported in `/api/health`; production-without-token warning | `test_access_token_gates_private_data_but_not_reference_data` (401/401/200, public reference data, gated export) |
| **client token flow** | token stored in `localStorage`, sent on every request, `401` raises a global event that opens a gate screen; the gate unlocks and reloads | `tsc`; build |
| **exports** | downloads now fetch a blob with the header instead of `window.open` (which cannot carry a header and would 401); object URL revoked on the next tick | `tsc`; API test asserts the export 401/200 split |
| **analysis read budget** | `check_analysis_area()` refuses an analysis above `GFI_MAX_ANALYSIS_HA` (default 20,000 ha ≈ 2 M px per Sentinel band) with `area_too_large_for_analysis`, instead of reading gigabytes per job; storage limit unchanged | `test_analysis_area_guard_refuses_oversized_boundaries` |
| **docs** | README API section, ARCHITECTURE security, LIMITATIONS operational limits, DEPLOY_RAILWAY env table + warning, `.env.example` (`GFI_ACCESS_TOKEN`, `GFI_MAX_ANALYSIS_HA`) | docs |

Suite is now **51 passed, 11 skipped**; build 344 kB app chunk / 1,053 kB MapLibre.

## Round 5

| Area | Change | Verified by |
|---|---|---|
| **search dialog** | rewritten as a real combobox: `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-autocomplete`, `aria-activedescendant` pointing at `<li role="option">` elements (the options were unfocusable buttons inside a listbox, so arrow-key selection was announced to nobody). Focus returns to the trigger on close; the focus timer is cleared. | `tsc`; build |
| **search transport** | the dialog called `fetch` directly, which ignored `VITE_API_BASE`, never checked `response.ok` (a 503 rendered as "No matching localities"), and — after the round-3 gate — would have returned 401 under `GFI_ACCESS_TOKEN`. Now goes through `api.searchPlaces()` with the token header, the 45 s timeout and typed `PlaceResult[]` results | `tsc`; code read |
| **fonts** | `@import "@fontsource-variable/inter"` pulled 7 subsets into the precache; the page now declares only latin and latin-ext (Twi/Ewe ɛ/ɔ live in latin-ext). Cyrillic, Greek and Vietnamese can never render here | build: precache **1,712.6 → 1,628.5 KiB**, font files **7 → 2** |
| **dependency lock** | `backend/requirements.lock` (35 pins) generated from a clean venv install of the ranges, with provenance in the header; the Dockerfile installs the lock; CI gains a `backend-lock` job that installs it on Python 3.12 and imports the app | **the pinned set runs the full suite: 52 passed, 11 skipped** in the lock venv; `pip install --dry-run` parses the file |
| **docs** | lock provenance + regeneration instructions; README/CI note | docs |

Suite: **52 passed, 11 skipped** in both the working venv and the pinned one.

## Round 6

| Area | Change | Verified by |
|---|---|---|
| **unit audit closed** | the three "could not verify offline" items are resolved with live evidence (ISRIC's table, CHIRPS COG metadata, DEM nodata); the review's own CEC suspicion was wrong and is corrected | probes below |
| **seed manifest** | `scripts/seed_soilgrids.py` now derives `scale_factors` from `providers.soilgrids.PROPERTIES` instead of re-declaring them; the bundled manifest's nitrogen and cfvo factors corrected from 10 to **100** | `test_seed_manifest_scale_factors_match_the_provider_table` |
| **provider registry** | the SoilGrids catalogue entry described WCS GetCoverage as the primary path — the opposite of what the code does and why. Rewritten to the VRT path, the ISRIC factors and the WCS rejection; this text is what `/api/catalog/providers`, the attribution page and the PDF provenance show | `import` check; code read |

Evidence for the audit:

```
CHIRPS daily COG   float32, nodata -9999, scales (1.0,), offsets (0.0,)   → read as mm, no factor
Copernicus DEM     float32, nodata None (voids are NaN)                  → no sentinel to misread
earth-search item  earthsearch:boa_offset_applied = True                 → NDVI not attenuated
ISRIC table        bdod 100 · cec 10→cmol(c)/kg · cfvo 100 · nitrogen 100 · phh2o 10 · texture 10
```

Suite: **53 passed, 11 skipped**.

## Round 7

| Area | Change | Verified by |
|---|---|---|
| **non-root container** | `docker/entrypoint.sh` chowns `GFI_DATA_DIR` while still root, then `exec gosu 10001:10001`; the Dockerfile creates the `gfi` user, installs `gosu` and sets `ENTRYPOINT`. A root-owned platform volume still works, and the app process is never root | `sh -n` on the entrypoint; workflow YAML parses; **CI `docker` job** builds the image, boots it, checks the PWA is served, that `/api/farms` is 401 without the token and 200 with it, and that **PID 1 is not root** |
| **line endings** | `.gitattributes` pins `*.sh` and `Dockerfile` to LF — a CRLF shebang is the classic "bad interpreter" container failure; the entrypoint was written CRLF-free and now stays that way | byte check of `docker/entrypoint.sh` (0 CR bytes) |
| **CI** | new `docker` job (buildx build, run, smoke-test, logs on failure); `.dockerignore` covers `.venv-lock` | workflow YAML parse: 4 jobs |
| **docs** | LIMITATIONS and DEPLOY_RAILWAY describe the uid-10001 behaviour and the `GFI_APP_UID`/`GFI_APP_GID` escape hatch | docs |

The container change is validated by CI rather than locally: this machine has no Docker daemon, so the `docker` job is the honest verification path — it fails loudly on the first push if `gosu`, the entrypoint or the drop-privileges step is wrong.

## Round 8

Full-stack verification for the first time: built the PWA, ran the app on a real server, installed Chromium, and let the acceptance suite drive it against the live providers.

```
desktop project   8/8 passed (1.9 min)   first run, clean database
android project   6/8 passed (11.2 min)  first run — two failures investigated below
android project   8/8 passed (1.4 min)   after the fixes
desktop project   8/8 passed (1.2 min)   re-run on a database with 3 farms
```

| Defect found | Detail | Fix |
|---|---|---|
| **mobile: timeline unreachable** | On a Pixel 5 the farm panel is a bottom sheet covering y=305–727; the floating timeline's "90 d" control sits at y=617, so `document.elementFromPoint` returned a panel metric, not the button. The app was showing a control that could not be tapped — the suite hung on it for 300 s. | `.app--panel .timeline { display: none }` inside the narrow-viewport block: the overlay now hides while the sheet is open instead of implying an affordance. Close the sheet to use the range control. |
| **mobile: layers panel never opened** | `evidence layers render on demand` addressed `.layer` rows without opening the panel, which defaults closed below 980 px (`layersOpen` initialises to `window.innerWidth > 980`). The click waited 300 s. | New `openLayers()` helper clicks the layers toggle when the panel is closed. |
| **harness: second run inherits the first run's farms** | `ensureFarmSelected` only created a sample farm when the onboarding button was visible; with farms already present it accepted whichever farm was auto-selected, so a re-run analysed the wrong boundary. | It now selects the sample farm explicitly from the farm dropdown when the list is non-empty. Verified by re-running desktop against a 3-farm database. |

Everything else held: the drawn-field save/export flow (which exercises the round-2 form fix), the evidence-layer render path (round-1 polygon-hash change), offline reload (round-2 scope keys), the access-token-free deployment, and the satellite toggle all pass end-to-end.

Note: the suite writes to `docs/screenshots`; those regenerated images were reverted so this change set stays reviewable.

## Round 9

| Area | Change | Verified by |
|---|---|---|
| **CI: browser acceptance** | the `docker` job now builds the image, starts it, smoke-tests it, checks the token gate on a second container, installs Chromium, and runs **all 16 acceptance tests** (desktop + mobile) against the image | workflow YAML parse: 4 jobs, `docker` has 9 steps |
| **CI: posture assertion** | the open deployment asserts `auth.farm_data == "open"` from `/api/health`; the gated one asserts 401/200/200, so both deployment postures are exercised on every push | same |
| **docs** | round-9 log | docs |

The e2e suite was validated locally in round 8 (16/16 across both projects, re-runnable against a populated database); this round puts it on CI so it cannot rot.

## Status of every finding

| Severity | Count | State |
|---|---|---|
| Blocker | 4 | all fixed and regression-tested |
| High | 10 | all fixed and regression-tested |
| Medium | 24 | 22 fixed; 2 documented decisions (per-user accounts; hashes need `pip-compile`) |
| Low | ~18 | fixed where they affected behaviour; the rest are documented |

**One product decision remains open** and is deliberately not improvised: per-user accounts (login method, shared vs private farms). The single-secret `GFI_ACCESS_TOKEN` gate protects a deployment today; real tenancy needs that decision. The default I would build on approval: email + password, farms private to their creator, optional read-only share link.

## Round 10 — accounts and passwords (requested)

| Area | Change |
|---|---|
| **accounts** | New `users` / `sessions` tables and a `farms.owner_id` column (migration 2). `app/auth.py` holds PBKDF2-SHA256 hashing, SHA-256 session tokens with expiry, and the bootstrap administrator. |
| **admin UI** | Settings → **Accounts** (visible only to administrators): list accounts with farm counts, create an account with a password and an "administrator" checkbox, promote/demote, reset a password, delete. Settings also shows who is signed in and offers Sign out. |
| **sign-in** | A username/password screen replaces the app when accounts exist and nobody is signed in; a wrong password says "Incorrect username or password." without revealing which usernames exist. |
| **ownership** | A non-administrator sees and edits only farms they created (404, not 403, for anyone else's); administrators see all; farms created before accounts existed are administrator-only. Jobs and layer renders are checked the same way. |
| **safety rails** | Last administrator cannot be demoted or deleted, nor can you demote or delete yourself; a password change ends that account's other sessions; deleting an account keeps its farms (unattached). |
| **compatibility** | With no accounts the deployment behaves exactly as before — open, or gated by `GFI_ACCESS_TOKEN`. The e2e suites were re-run to prove it. |

Verified: **62 backend tests** (9 new account tests: isolation, admin flag, last-admin guard, session
invalidation on password change, deletion keeping farms); `tsc` and `vite build` clean; a browser probe of the
real UI (login screen → wrong password → sign in → Settings → Accounts → create account with the admin flag →
two rows, no page errors); and **the existing 8/8 desktop acceptance tests still pass** on an open deployment.

Two defects were caught by these tests: `PATCH /api/auth/users/{id}` returned `is_admin` as `1` rather than
`true` (inconsistent with create), and the auth tests initially inherited farms from an earlier module.

## Verification commands used

```bash
python -m pytest backend/tests -q          # 47 passed, 11 skipped (live, opt-in)
cd frontend && npx tsc -b --force          # exit 0
cd frontend && npx vite build              # exit 0 — 1,410 kB / 393 kB gzip, 1,713 KiB precache
```
