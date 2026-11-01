# Ghana crop reference database - sources and maintenance notes

Companion document for [`backend/data/ghana_crops.json`](./ghana_crops.json).

**This is a planning reference, not extension advice.** It describes indicative
climate and soil envelopes for 20 crops grown in Ghana, compiled from published
sources. It contains **no yields, no prices, no fertilizer rates and no pesticide
recommendations**. It cannot tell you what a particular field will produce.

Local variety, local soil test results and local extension advice always take
precedence over the ranges in this file. Remote sensing (polygon area, CHIRPS
rainfall, SoilGrids pH and texture, Copernicus slope) gives sampled estimates with
real error; it is never a guarantee of yield.

---

## 1. Sources

All URLs below were opened during compilation on 2026-10-31. If a link rots,
re-check the figure against a current edition before editing the JSON.

| id | title | publisher | url | licence | what it informed |
|---|---|---|---|---|---|
| `fao_ecocrop` | Ecocrop database - crop environmental requirements and data sheets | FAO, Land and Water Division | https://ecocrop.apps.fao.org/ecocrop/srv/en/home | FAO content; factual ranges reused with attribution, check FAO terms before redistribution | Temperature optima and absolutes, annual rainfall envelopes, soil pH optima and absolutes, salinity and fertility tolerance, crop-cycle spans, life span (annual/perennial), scientific names |
| `fao56` | Crop evapotranspiration - Guidelines for computing crop water requirements (FAO Irrigation and Drainage Paper 56) | FAO (Allen, Pereira, Raes, Smith), Rome 1998 | https://www.fao.org/3/x0490e/x0490e00.htm | FAO 1998, all rights reserved; cited and paraphrased only | Lengths of crop development stages (Table 11) and therefore growth-stage day boundaries, typical cycle lengths, and the water-stress/critical-stage logic |
| `fao_cropcalendar_gh` | FAO Crop Calendar - Ghana | FAO | https://cropcalendar.apps.fao.org/?isocode=GHA | FAO; factual calendar data used with attribution | Per-zone sowing and harvest windows and growing periods for cassava, rice, yam, groundnut, cowpea, sorghum, millet, soybean, sweet potato, cocoyam and vegetables; zone rainfall bands quoted in the record comments |
| `fao_giews_ghana` | GIEWS Country Brief: Ghana | FAO GIEWS | https://www.fao.org/giews/countrybrief/country.jsp?code=GHA | FAO; summarised with attribution | Season structure: southern major season about March-July, southern minor season, unimodal northern sowing May-July |
| `mofa_gh_facts` | Agriculture in Ghana: Facts and Figures 2019 | Ministry of Food and Agriculture, Ghana - SRID | https://mofa.gov.gh/site/images/pdf/2019%20AGRICULTURE%20FACT%20AND%20FIGURES.pdf | Government of Ghana publication; read for context, no tables reproduced | National agro-ecological zone framing and which crops matter in each zone |
| `ghana_seed_policy_2013` | National Seed Policy (Republic of Ghana) | Ministry of Food and Agriculture, Ghana, Accra, May 2013 (copy on FAOLEX) | https://faolex.fao.org/docs/pdf/gha169581.pdf | Government of Ghana policy document via FAOLEX; cited for factual zone rainfall | Mean annual rainfall by agro-ecological zone (section 2.1.3, Table 1, attributed there to SRID/MoFA and the Meteorological Services Department) |
| `icco_cocoa` | Growing Cocoa | International Cocoa Organization | https://www.icco.org/growing-cocoa/ | ICCO; summarised with attribution | Cocoa rainfall preference (1,500-2,000 mm, dry spells under 100 mm/month not exceeding about three months), temperature maxima and minima, shade requirement in early years |
| `cocobod` | Ghana Cocoa Board - institutional website | Ghana Cocoa Board | https://cocobod.gh/ | COCOBOD; institutional reference only | Confirms COCOBOD as the national cocoa regulator and owner of extension material. Deliberately not used for figures: the site publishes producer prices, which are excluded from this file |
| `gmet` | Ghana Meteorological Agency - institutional website | Ghana Meteorological Agency | https://meteo.gov.gh/ | GMet; institutional reference only | Pointer to the national authority for seasonal forecasts. No GMet figures or text are reproduced; check the seasonal outlook before fixing planting dates |

### Notes on what was actually readable

- **FAO Crop Calendar** is a JavaScript application, so the readable data came from
  the JSON service its own front-end calls:
  `https://api-cropcalendar.apps.fao.org/api/v1/countries/GH/cropCalendar?language=en`
  (country code `GH` in that API, `GHA` in the web URL; `lastUpdated` 26/02/2021,
  58 records). Ghana's file has **no field-maize row** (only "Corn, sweet"), and
  **no cocoa or plantain rows**.
- Some FAO calendar rows are internally inconsistent - onion shows harvest before
  sowing, tomato and okra are listed as all-year crops, and the soybean growing
  period is given as 120-215 days. Those rows were not used for windows; the
  deviation is recorded in the affected crop's `seasons_note`.
- **Ecocrop** gives *annual* rainfall, not seasonal rainfall. Every
  `seasonal_rainfall_mm` value in the JSON is a narrower, Ghana-season estimate
  derived from FAO-56 water requirements plus local practice, and is **not** an
  Ecocrop value.
- **FAO-56 Table 11** stage lengths are illustrative regional averages and are not
  variety-specific; the JSON says so in each crop's `limitations`.
- The **MoFA 2019 PDF** is a 17 MB scanned-style publication. Its text layer was
  extracted for context only; no production, price or input-rate figures were copied.
- **Local names** (`tw`, `ee`) are common-usage renderings supplied for the app's
  UI, not dictionary-verified. Only names judged safe were included; where there
  was doubt the language key was omitted rather than guessed.

### Ecocrop data sheet ids used

Reviewers can re-open any row at
`https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=<id>`.

| crop | Ecocrop id | scientific name in Ecocrop |
|---|---|---|
| maize | 10982 | Zea mays |
| cassava | 1420 | Manihot esculenta |
| rice_upland | 1574 | Oryza sativa |
| rice_lowland | 1574 | Oryza sativa |
| yam | 942 | Dioscorea rotundata |
| plantain | 7849 | Musa acuminata x balbisiana |
| cocoa | 2074 | Theobroma cacao |
| groundnut | 2199 | Arachis hypogaea |
| cowpea | 2153 | Vigna unguiculata |
| sorghum | 48747 | Sorghum bicolor |
| millet | 8418 | Pennisetum glaucum |
| soybean | 1150 | Glycine max |
| tomato | 1379 | Solanum lycopersicum |
| pepper | 618 | Capsicum annuum |
| okra | 289 | Abelmoschus esculentus |
| cashew | 401 | Anacardium occidentale |
| oil_palm | 972 | Elaeis guineensis |
| sweet_potato | 1265 | Ipomoea batatas |
| garden_egg | 9825 | Solanum aethiopicum |
| onion | 364 | Allium cepa |

---

## 2. Evidence quality

Each crop carries one `evidence_quality` value:

- `verified_source` - the key numbers were read directly from at least two of the
  sources above (normally Ecocrop plus FAO-56 or the FAO Ghana calendar).
- `secondary_reference` - the crop is covered by a source, but a material part of
  the entry is inferred rather than read (plantain: Ghana calendar has no row, so
  windows are left null and the entry leans on Ecocrop for the hybrid Musa group).
- `expert_reference` - no directly verified source for the numbers; used only where
  a crop is in the list but unpublished for Ghana. No crop currently carries this
  label; if you add one, say so in the pull request.

Least certain values in the current file, in order:

1. `seasonal_rainfall_mm` for every crop. Ecocrop reports annual rainfall and the
   JSON needs a seasonal figure; these are derived and deliberately widened.
2. Seasonal windows for **maize, tomato, pepper, okra, garden_egg, onion**, where
   the FAO Ghana calendar row was missing or unusable. These are documented
   syntheses, not read-off values.
3. Zone rainfall bands. The Seed Policy table gives *point means* per zone; the
   JSON bands are widened around those points to cover inter-annual variation and
   the wetter/drier edges of each zone.
4. `common_stresses` and `management_notes` are qualitative and unsourced in the
   detailed sense: they name widely reported pests and practices, not measured
   pressure. They are **not** a spray guide.
5. `local_names` (see above).

---

## 3. How to update this file

1. **Open the source.** Do not edit a number from memory, a blog post or a model
   output. If you cannot open the page, do not cite it.
2. **Never add** prices, yields, fertilizer rates, pesticide active ingredients or
   dosages, or hectare-level revenue. The validator in step 7 rejects several of
   these tokens automatically.
3. **Keep the structural invariants** (the validator checks all of them):
   - exactly the 20 required crop ids, in the required order;
   - every crop has `id, name, local_names, scientific_name, perennial,
     cycle_days, growth_stages, water, soil, temperature_c, slope_max_percent,
     seasons, agro_ecological_suitability, common_stresses, management_notes,
     limitations, evidence_quality, source_ids`;
   - growth stages ascending, each `start_day < end_day`, and
     `last end_day <= cycle_days.max + 30`;
   - `cycle_days.min <= typical <= max`;
   - `soil.ph[0] <= ph_optimal[0] < ph_optimal[1] <= soil.ph[1]`;
   - `temperature_c.mean_min <= optimal[0] < optimal[1] <= mean_max`;
   - `agro_ecological_suitability` has exactly the five zone keys.
4. **Seasons.** Keep `[[month, day], [month, day]]` pairs and keep every window
   **non-wrapping** (start not later than end in calendar order) so that simple
   consumers do not mis-sort it. If a real window crosses New Year, cut it at
   31 December and explain the truncation in `seasons_note`. Harvest windows that
   fall in the *following* year (cassava, onion) are legitimate and are documented
   in `seasons_note`.
5. **Perennials** (`cocoa`, `oil_palm`, `cashew`, `plantain`): `perennial: true`,
   `seasons: null`, a non-empty `seasons_note`, and `cycle_days` meaning **time to
   first commercial harvest** - not the life of the planting. Describe immature and
   mature phases in the stage notes; the mature phase is not modelled.
6. **Cite what you used.** Add a `sources` entry with a URL you actually opened,
   today's `accessed` date, and a note saying what the source informed. Add its id
   to the affected crops' `source_ids` (and to the zone's, if relevant).
7. **Re-validate** after every edit (Python 3, no dependencies):

   ```python
   import json
   db = json.load(open("backend/data/ghana_crops.json", encoding="utf-8"))
   crops = db["crops"]
   assert db["schema_version"] == 1
   assert len(crops) == 20, len(crops)
   assert [c["id"] for c in crops] == [
       "maize", "cassava", "rice_upland", "rice_lowland", "yam", "plantain",
       "cocoa", "groundnut", "cowpea", "sorghum", "millet", "soybean", "tomato",
       "pepper", "okra", "cashew", "oil_palm", "sweet_potato", "garden_egg", "onion"]
   for c in crops:
       assert c["source_ids"] and all(s in db["sources"] for s in c["source_ids"]), c["id"]
       cyc = c["cycle_days"]
       assert cyc["min"] <= cyc["typical"] <= cyc["max"]
       prev = None
       for st in c["growth_stages"]:
           assert st["start_day"] < st["end_day"], (c["id"], st["name"])
           assert prev is None or st["start_day"] >= prev, (c["id"], st["name"])
           prev = st["end_day"]
       assert prev <= cyc["max"] + 30, c["id"]
       so, t = c["soil"], c["temperature_c"]
       assert so["ph"][0] <= so["ph_optimal"][0] < so["ph_optimal"][1] <= so["ph"][1], c["id"]
       assert t["mean_min"] <= t["optimal"][0] < t["optimal"][1] <= t["mean_max"], c["id"]
       if c["perennial"]:
           assert c["seasons"] is None and c["seasons_note"], c["id"]
   print("ok", len(crops), "crops,", len(db["sources"]), "sources")
   ```

8. **Refresh metadata.** Update `generated_at` (ISO8601 UTC) and `accessed` on any
   source you re-checked.
9. **Bump `schema_version`** only if the shape changes (new required key, changed
   meaning of an existing one). Adding a crop or a source is not a schema change;
   removing a required crop id is.

---

## 4. Known gaps worth closing

- **Field maize** has no FAO Ghana crop-calendar row. A MoFA/CSIR-SARI published
  maize calendar for the northern and southern zones would let the maize windows
  move from `secondary` derivation to `verified`.
- **Plantain** is not in the FAO Ghana calendar; a verified bunch-cycle source
  (e.g. a CSIR or IITA plantain guide) would upgrade it from
  `secondary_reference`.
- **Zone rainfall bands** should be replaced by CHIRPS-derived normals per zone
  once the app can compute them; that would make the bands reproducible and
  versionable rather than hand-widened.
- **Cocoa and oil palm mature-phase** behaviour (bunch/pod cycles, shade
  interactions) is described qualitatively only. If the rule engine ever scores
  mature perennials, it needs a separate, explicitly modelled phase.
