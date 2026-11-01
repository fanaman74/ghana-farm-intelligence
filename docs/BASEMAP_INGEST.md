# Ghana basemap ingest (OpenStreetMap → local seed)

Reproducible build of the self-hosted Ghana basemap and locality-search seed used by
the Gh-farmers backend. Everything is derived from the Geofabrik Ghana OpenStreetMap
extract; **no map tiles are downloaded, generated, or packaged**.

- Script: [`scripts/build_ghana_basemap.py`](../scripts/build_ghana_basemap.py)
- Input: `backend/data/raw/ghana-latest.osm.pbf` (large binary, shipped out-of-band)
- Outputs:
  - `backend/data/seed/ghana_places.json.gz`
  - `backend/data/seed/ghana_roads.geojson.gz`
  - `backend/data/seed/ghana_water.geojson.gz`


## 1. Source and licence

| item | value |
| --- | --- |
| Extract page | https://download.geofabrik.de/africa/ghana.html |
| Download URL | https://download.geofabrik.de/africa/ghana-latest.osm.pbf |
| Local file | `backend/data/raw/ghana-latest.osm.pbf` |
| Size | 116,754,371 bytes (111.3 MiB) |
| File mtime (UTC) | `2026-10-31T15:42:07Z` |
| SHA-256 | `2b668c16194c991acd112f8f5c933bb9ca53ec86e1200ec88afdb39f567f0425` |
| Snapshot taken | 2026-10-31 |
| Licence | **ODbL 1.0** (Open Database License) |

The data is © OpenStreetMap contributors and is made available under the Open
Database License, version 1.0. Any public use of these files, or of a derived
database, must:

1. **Attribute** OpenStreetMap: “© OpenStreetMap contributors”, with a link to
   <https://www.openstreetmap.org/copyright>.
2. **Share-alike**: if you publicly use a derived database, offer it under ODbL 1.0.
3. Keep copyright/licence notices intact.

The three seed files embed the strings `source = "OpenStreetMap via Geofabrik
(ODbL 1.0)"`, `source_url = "https://download.geofabrik.de/africa/ghana.html"` and
`licence = "ODbL 1.0 - (c) OpenStreetMap contributors"` in their top-level metadata
so attribution travels with the data.

> No tiles from `tile.openstreetmap.org` (or any other tile server) are downloaded,
> rendered, cached, or packaged by this pipeline. The outputs are plain GeoJSON /
> JSON vectors derived from the PBF. The OpenStreetMap tile usage policy is
> therefore not engaged.


## 2. Requirements

- Python 3.12 with `pyosmium`, `shapely` (2.x) installed.
- This workspace uses the bundled interpreter:

```powershell
C:\Users\fred\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
```

- Network access is needed **only** to download the PBF. Once the file is present
  the build is fully offline.
- Runtime on the development machine: ~95 s (18.9 M nodes, 2.88 M ways, 19,479
  relations read). Memory use is dominated by pyosmium's in-process node-location
  cache (flex_mem) for the 18.9 M nodes.


## 3. Exact commands

From `C:\Users\fred\Downloads\Gh-farmers`:

```powershell
# 1) One-time download (skipped automatically if the PBF already exists and is >50 MB)
& "C:\Users\fred\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" `
    scripts\build_ghana_basemap.py --download

# 2) Build all three seed files (offline, re-runnable)
& "C:\Users\fred\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" `
    scripts\build_ghana_basemap.py

# Optional: custom paths
& "...\python.exe" scripts\build_ghana_basemap.py `
    --pbf backend\data\raw\ghana-latest.osm.pbf `
    --out-dir backend\data\seed
```

With a normal `python` on `PATH`:

```powershell
python scripts/build_ghana_basemap.py --download   # first time only
python scripts/build_ghana_basemap.py              # rebuild
```

The script exits non-zero if verification fails. It does not start any server.


## 4. Output summary

Build timestamp: **`2026-10-31T16:01:36Z`** (the same `generated_at` is written to
all three files).

| file | size (gzip) | count | content bbox (min lat, min lon, max lat, max lon) |
| --- | ---: | ---: | --- |
| `backend/data/seed/ghana_places.json.gz` | 273,457 B (267 KiB) | 9,774 places | 4.739811, -3.246019, 11.148113, 1.204468 |
| `backend/data/seed/ghana_roads.geojson.gz` | 293,297 B (286 KiB) | 4,063 road features | 4.740440, -3.300000, 11.176520, 1.211520 |
| `backend/data/seed/ghana_water.geojson.gz` | 346,942 B (339 KiB) | 728 water features | 4.796080, -3.300000, 11.200000, 1.071530 |

SHA-256 of the generated files:

```
ghana_places.json.gz      c5d072b354c248339c033afdbb85f53267f6097b321465165dd9663397543018
ghana_roads.geojson.gz    cc4602209bdd6e2bc4d44ad24bb3d46e14f2d6d1edfba29b6709d4d8fc48989c
ghana_water.geojson.gz    b6335bfa7f45e1dac9d1c44acd3a4c016c860a3a43ee7b17d9ba26f177c2dce9
```

Gzip members are written with `mtime=0`, so no wall-clock data leaks into the
gzip header. The decompressed JSON payload is identical across runs over the same
PBF apart from the `generated_at` timestamp; the compressed bytes (and therefore
the hashes above) change when the build is re-run.

All coordinates are WGS84 `[lon, lat]`, rounded to 5 decimal places for roads and
water and 6 decimal places for places. No `NaN`/`Infinity` values are emitted, and
every coordinate is inside the configured Ghana envelope.


## 5. Places seed (`ghana_places.json.gz`)

### 5.1 Shape

```json
{
  "generated_at": "2026-10-31T16:01:36Z",
  "source": "OpenStreetMap via Geofabrik (ODbL 1.0)",
  "source_url": "https://download.geofabrik.de/africa/ghana.html",
  "licence": "ODbL 1.0 - (c) OpenStreetMap contributors",
  "bbox": {"min_lat": 4.5, "min_lon": -3.3, "max_lat": 11.2, "max_lon": 1.3},
  "counts": {"total": 9774, "by_type": {"city": 14, "...": "..."}},
  "places": [
    {
      "id": "osm:node/1822008168",
      "name": "1000 Acre",
      "type": "hamlet",
      "lat": 6.508558,
      "lon": -2.592602,
      "region": "Ahafo",
      "district": "Asunafo South District",
      "population": null,
      "vicinity": null,
      "addr_region": null,
      "addr_district": null
    }
  ]
}
```

- `id` is `osm:node/<id>`, `osm:way/<id>` or `osm:relation/<id>`.
- `type` is one of `city`, `town`, `village`, `hamlet`, `suburb`,
  `neighbourhood`, `locality`, `isolated_dwelling`, `farm`, plus the synthetic
  `region_centre` and `district_centre`.
- `population` is parsed from the OSM `population` tag when numeric (commas and
  other characters removed), otherwise `null` (102 entries have a value).
- `vicinity` stores the OSM `is_in` tag; `addr_region` / `addr_district` store
  `addr:region` / `addr:district`. **The Ghana extract contains none of these
  tags**, so all three fields are `null` for every entry. They are kept in the
  schema so a future extract that adds them is passed through automatically.
- `region` uses the official short region name (`Ashanti`, `Greater Accra`, …).
- Entries are sorted by case-insensitive `name`.

### 5.2 How records are produced

1. **Nodes** with `place` in the allowed set, a non-empty `name`, and a coordinate
   inside `lat 4.5..11.2`, `lon -3.3..1.3`.
2. **Ways** with an allowed `place` tag: position is the arithmetic centroid of the
   way's member-node coordinates.
3. **Relations** with an allowed `place` tag: centroid of all member-way node
   coordinates (relations with no member-way geometry are skipped).
4. **Administrative centres**: for `boundary=administrative` relations with
   `admin_level=4` or `6` that have a name, one synthetic entry at the relation's
   polygon centroid:
   - `admin_level=4` → `type="region_centre"`;
   - `admin_level=6` → `type="district_centre"`.
   Only Ghana's 16 regions (matched against the official region-name list) are used
   at level 4; level-6 districts are kept only when their centroid falls inside an
   assembled Ghanaian region polygon. A centre is not added when a place with the
   same name is already mapped within ~0.05° of that centroid, and duplicates are
   removed by `(name, lat/lon rounded to 0.01)`.

Counts by type:

| type | count |
| --- | ---: |
| village | 6,219 |
| hamlet | 1,665 |
| suburb | 616 |
| town | 550 |
| district_centre | 262 |
| isolated_dwelling | 210 |
| neighbourhood | 138 |
| locality | 72 |
| region_centre | 16 |
| city | 14 |
| farm | 12 |
| **total** | **9,774** |

All 16 regions are represented. 9,614 places (98.4 %) have both `region` and
`district`; 160 entries (mostly across the Ivorian, Togolese and Burkinabè border
areas inside the rectangular envelope) fall outside the assembled region polygons
and therefore have `region = null` and `district = null`.

### 5.3 Why the file has 9,774 entries, not “tens of thousands”

This is the real content of the extract, not a filtering accident:

- 12,446 nodes in the extract carry a `place=*` tag.
- 2,935 of them have **no `name`** (they are Landsat-derived imports such as
  `{"place": "village", "source": "landsat"}`) and are skipped as unnamed.
- 60 carry a `place` value outside the requested set (`islet`, `island`, `square`,
  `plot`, `quarter`, `state`, `region`, `city_block`, …).
- 523 ways carry a `place` tag, but most are `place=islet` / `island` / `square`;
  only 37 named ways have an allowed value.
- 8 place relations have an allowed value and usable member geometry.
- 278 administrative centres are added (16 regions + 262 districts).

9,451 named nodes + 37 ways + 8 relations + 278 centres = **9,774**.

### 5.4 Region / district assignment

The extract has **zero** `is_in`, `addr:region` or `addr:district` tags on places,
so region/district cannot come from tags. Instead the script assembles the OSM
administrative boundary polygons from their member ways (node-ref ring stitching)
and performs point-in-polygon lookups:

- `admin_level=4` polygons → region name (16 Ghanaian regions; neighbouring
  countries' level-4 relations are excluded by an official-name whitelist).
- `admin_level=6` polygons → district name (303 level-6 relations in the extract;
  265 polygons assembled, 262 passed the Ghana region-polygon test and are used as
  districts).
- 51 administrative relations could not be assembled (38 level-6 and 13 level-4
  relations, mostly foreign or with incomplete member rings) and were skipped; 97
  ring chains were either closed across small gaps (<1 km, usually extract
  clipping) or left open.

This is a deliberate improvement over leaving `region`/`district` null for almost
every row; it is documented here as a derivation, not as raw OSM tag data.


## 6. Roads seed (`ghana_roads.geojson.gz`)

`highway` values included: `motorway`, `trunk`, `primary`, `secondary`,
`tertiary` plus `motorway_link`, `trunk_link`, `primary_link`, `secondary_link`,
`tertiary_link`.

Processing:

1. Read every highway way with pyosmium node locations (missing nodes → skip).
2. **Merge first**: contiguous ways with identical `(class, name, ref, surface)`
   are joined with `shapely.ops.linemerge`. OSM splits roads into short ways at
   every junction; merging keeps the network continuous and stops the 200 m cut
   from deleting thousands of junction stubs. Ways only merge when they share
   endpoints, so same-named roads in different towns stay separate.
3. Simplify each merged line with `shapely.simplify(0.0005, preserve_topology=True)`
   (~55 m at Ghana's latitude).
4. Clip to the Ghana envelope, round to 5 decimals.
5. Drop features shorter than 200 m.

Results:

- 12,904 raw highway segments → 2,015 merge groups → **4,063 features**.
- 2,738 short/clipped fragments dropped by the 200 m rule.
- Total mapped length ≈ 30,061 km.

| class | features |
| --- | ---: |
| motorway | 9 |
| motorway_link | 24 |
| trunk | 467 |
| trunk_link | 54 |
| primary | 454 |
| primary_link | 10 |
| secondary | 771 |
| secondary_link | 12 |
| tertiary | 2,262 |
| tertiary_link | 0 |
| **total** | **4,063** |

Feature properties: `class`, `name`, `ref`, `surface` (all strings or `null`).
Geometry is `LineString`, or `MultiLineString` where clipping split a feature.

Size control: the file is 0.29 MB gzipped, far below the 10 MB target, so **no
class was dropped**. The script contains the fallback ladder required by the task
(drop `tertiary_link` first, then `tertiary`) and would log it if the size limit
were exceeded; it was not triggered.

The `tertiary_link` count is 0 because all 106 such ways were shorter than 200 m
after merging/clipping and were dropped by the minimum-length rule.


## 7. Water seed (`ghana_water.geojson.gz`)

Included:

- `natural=water` **ways** with a closed ring and area > 1 km².
- `natural=water` **relations** (multipolygon) with area > 1 km², assembled from
  member ways — this is how Lake Volta, Kpong Reservoir, Bui Reservoir, Weija
  Reservoir, Keta Lagoon, etc. are included.
- `waterway=river` ways when the river has a name **or** is at least 10 km long.
- `waterway=river` relations with a name (member lines merged with `unary_union`).

`kind` is `reservoir` when the feature carries `water=reservoir` /
`landuse=reservoir` or its name contains “reservoir”/“dam”; otherwise `lake` for
water bodies and `river` for waterways.

Processing: simplify with `0.0005` (~55 m), clip to the envelope, round to 5
decimals. Result: **728 features** — 38 lakes, 9 reservoirs, 681 rivers (0.35 MB
gzipped, well under the 3 MB target).

Dropped: 2,935 water polygons under 1 km², 1 unclosed `natural=water` way, and 864
unnamed river ways shorter than 10 km. Lake Volta's OSM multipolygon produces one
polygon of ≈6,600 km² after simplification/clipping (the official surface area is
larger; this is the geometry present in OSM).

Feature properties: `kind`, `name` (`null` allowed for large unnamed water bodies).
Geometry is `Polygon`/`MultiPolygon` or `LineString`/`MultiLineString`.


## 8. Verification performed by the script

After writing, the script reloads each file with `gzip` + `json` and prints:

- compressed file size;
- feature/place count;
- three sample records;
- the content bounding box;
- the number of places/features containing `NaN`/infinite coordinates or
  coordinates outside the Ghana envelope.

The build fails (exit code 1) if any coordinate is non-finite or outside
`lat 4.5..11.2`, `lon -3.3..1.3`. Last run: **verification OK**, 0
out-of-bbox/non-finite coordinates in all three files.

Notes on the envelope: the Geofabrik extract contains complete ways/relations
that cross Ghana's borders, and the requested envelope is a rectangle that
includes thin border strips of Togo, Burkina Faso and Côte d'Ivoire. Those
border features are kept (and are inside the envelope) rather than clipped to a
national polygon. This is why a handful of roads/rivers/places near the border
belong to a neighbour, and why 160 places have `region = null`.


## 9. Refresh procedure

```powershell
# Replace the extract (downloads only if missing or <50 MB)
& "C:\Users\fred\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" `
    scripts\build_ghana_basemap.py --download

# Rebuild the three seed files (~95 s) and re-run verification
& "C:\Users\fred\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" `
    scripts\build_ghana_basemap.py
```

To force a fresh download, delete `backend/data/raw/ghana-latest.osm.pbf` first.
The script writes `*.tmp` files and atomically replaces the previous outputs, so a
failed run leaves the old seed intact. Commit the three `.json.gz` / `.geojson.gz`
files when they change. `generated_at` changes on every run and can be used by the
backend to invalidate in-memory locality caches.

The PBF is ~111 MiB. If the repository excludes large binaries, ship it
out-of-band or re-download it with `--download`; only the three compressed seed
files are application artefacts.


## 10. Known limitations / deviations

- **Place count**: 9,774, below the task's “tens of thousands” estimate. Section
  5.3 explains that this is the complete set of named, allowed `place=*` features
  in the extract.
- **Region/district are derived**, not OSM `addr:*`/`is_in` tags (which are absent
  in this extract). Border and gap areas can be `null`. See 5.4.
- **Naming differs from the separate ADM1/ADM2 seed**: `region` uses the short
  official name (`Ashanti`), while `ghana_adm1.geojson` stores `Ashanti Region`;
  `district` keeps the raw OSM relation name (`Bia West District`), which can
  differ in suffix/spelling from `ghana_adm2.geojson`. Normalize before joining
  the two datasets.
- **Administrative centres** use polygon centroids (or member-way means when a
  polygon could not be assembled), not the OSM `admin_centre` role node. 262 of
  303 `admin_level=6` relations produced a usable Ghanaian centre.
- **Roads**: merge + 200 m cut as described; no class dropping was necessary.
  Border roads inside the rectangular envelope are retained.
- **Water**: small ponds and unnamed short streams are dropped as specified;
  Lake Volta is classified as `reservoir`.
- **No tiles** of any kind are fetched or bundled — see Section 1.


## 11. Quick inspection commands

```powershell
# Counts and metadata without extracting the whole file
& "...\python.exe" -c "import gzip,json; d=json.loads(gzip.decompress(open(r'backend/data/seed/ghana_places.json.gz','rb').read())); print(d['generated_at'], d['counts'])"

# Re-run only the built-in verification
& "...\python.exe" -c "import sys; sys.path.insert(0,r'scripts'); import build_ghana_basemap as b; from pathlib import Path; b.verify_outputs(Path('backend/data/seed/ghana_places.json.gz'), Path('backend/data/seed/ghana_roads.geojson.gz'), Path('backend/data/seed/ghana_water.geojson.gz'))"
```
