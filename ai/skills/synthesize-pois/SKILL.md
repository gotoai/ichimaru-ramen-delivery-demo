---
name: synthesize-pois
description: >-
  Synthesize Ichimaru's POIs — competitor stores and home buildings — by
  population-weighted random sampling (the same Location sampling algorithm as
  synthesize-stores), driven entirely by config/config.yaml. Per prefecture in
  synthetics/competitors/numbers and synthetics/home_buildings/numbers, samples
  that many ooaza with probability proportional to 2020 Census population
  (with replacement — POIs may share an ooaza), places each at a uniform random
  point inside the sampled ooaza polygon, draws sales baselines / unit counts and
  open/close dates, and writes
  DATA/s03_primary/competitor.tsv and DATA/s03_primary/home_building.tsv.
  Reproducible via synthetics/random_seed. Use when asked to synthesize, generate,
  sample, refresh, or bootstrap the demo's competitors, home buildings, or POIs.
---

# Synthesize POIs

Generates two primary-data POI layers from real Japanese geography, reusing the
**Location sampling algorithm** of the [`synthesize-stores`](../synthesize-stores/SKILL.md)
skill: each POI sits in a 大字・町 (ooaza) chosen with probability proportional to
that ooaza's 2020 Census population. Unlike stores (which sit on the ooaza
centroid), each POI is dropped at a **uniform random point inside the sampled
ooaza polygon**. Unlike stores, POIs are also sampled **with replacement** (the
same ooaza may host several competitors / buildings), and each
row carries open/close dates.

## What it produces

Both files are UTF-8, tab-separated, under `DATA/s03_primary/`, **overwritten**
each run.

`competitor.tsv` — competing noodle/food stores:

| column | meaning |
| --- | --- |
| `prefecture` | English prefecture name (e.g. `Tokyo`) |
| `competitor_name` | `競合_<都道府県名><市区町村名><大字・町名>店_<seq>` |
| `latitude` / `longitude` | uniform random point inside the ooaza polygon (6 dp) |
| `weekday_sale_baseline` | uniform sample in `synthetics/competitors/weekday_sales_baseline` |
| `weekend_sale_baseline` | uniform sample in `synthetics/competitors/weekend_sales_baseline` |
| `open_date` | uniform random date in the competitor history window |
| `end_date` | `open_date + business_duration` days, or `9999-99-99` if still open |

`home_building.tsv` — residential buildings/blocks:

| column | meaning |
| --- | --- |
| `prefecture` | English prefecture name |
| `home_building_name` | `住宅_<都道府県名><市区町村名><大字・町名>レジデンス_<seq>` |
| `latitude` / `longitude` | uniform random point inside the ooaza polygon (6 dp) |
| `unit` | integer, uniform sample in `synthetics/home_buildings/unit_range` |
| `open_date` | uniform random date in the home-building history window |
| `end_date` | always `9999-99-99` (a home building never closes) |

`<seq>` is a per-file, zero-padded running index that keeps every row's name
unique even when an ooaza is sampled more than once.

## How to run it

From the repo root, with the project `.venv` active:

```bash
source .venv/bin/activate
pip install -r requirements.txt          # first time only (pyshp, PyYAML)
python ai/skills/synthesize-pois/scripts/synthesize_pois.py
```

Reproducible via `synthetics/random_seed` in `config/config.yaml` (competitors are
generated first, then home buildings, from a single seeded RNG). Options:
`--repo-root <path>` and `--config <path>` (both auto-detected by default).

## Inputs

- `config/config.yaml`:
  - `synthetics/random_seed`
  - `synthetics/competitors/{numbers, weekday_sales_baseline,
    weekend_sales_baseline, business_duration}`
  - `synthetics/home_buildings/{numbers, unit_range}`
  - `numbers` is a map of **Japanese prefecture name → count**.
- `DATA/s02_intermediate/regional_population.tsv` — from `retrieve-regional-population`.
- `DATA/s02_intermediate/geoshape_<NN>/r2ka<NN>.shp` (+ `.dbf`) — from `retrieve-regional-geoshapes`.

Run `make base-data` first if those intermediate files are missing.

## Sampling algorithm, step by step

For each prefecture in the relevant `numbers` map (mapped Japanese name → 2-digit
JIS code):

1. **Build the pool.** Load the level-3 (大字・町) population rows and assemble each
   ooaza's polygon geometry — all its 字・丁目 rings, bounding box, and (as a
   fallback) the area-weighted centroid — keep ooaza with population `> 0` and
   geometry, normalise populations to weights summing to `N = 10,000,000`, and build
   the cumulative partition. (Pools are computed once and reused across both layers.)
2. **Sample `n` POIs with replacement.** For each, draw `u ~ Uniform[0, N)` and
   binary-search the partition for the ooaza — **no de-duplication**, so the same
   ooaza can recur.
3. **Name + place.** Name = `<prefix><都道府県名><市区町村名><大字・町名><suffix>_<seq>`
   (competitors: prefix `競合_`, suffix `店`; homes: prefix `住宅_`, suffix
   `レジデンス`); coordinates = a **uniform random point inside the ooaza polygon**,
   found by rejection sampling in the bounding box with an even-odd point-in-polygon
   test (holes and multi-part shapes handled), falling back to the centroid only if
   no interior point is found.
4. **Draw magnitudes.** Competitors: weekday & weekend baselines (two uniforms).
   Homes: a single `unit` count (rounded to an integer).
5. **Draw dates.** `open_date` = a uniform random date in the history window. For
   competitors, `end_date = open_date + Uniform[business_duration]` days, replaced
   by `9999-99-99` when it falls after the cutoff (i.e. still open today); for
   homes, `end_date` is always `9999-99-99`.

### Time windows (computed in JST)

The `time_horizon` config entries are prose rules; the skill implements them
directly:

- **cutoff** = the date **two days before today** (Asia/Tokyo).
- **history window** (both competitor and home building) =
  `Jan 1 of (cutoff_year − 5)` .. `cutoff`.

`CUTOFF_DAYS_BEFORE_TODAY` and `HISTORY_START_YEARS_BEFORE` constants at the top of
[scripts/synthesize_pois.py](scripts/synthesize_pois.py) mirror those rules; update
them there if `time_horizon` changes.

## Notes & maintenance

- **`numbers` keys are Japanese prefecture names** (`東京都`, …); the `prefecture`
  output column is the English display name, matching `store.tsv`.
- **Conflicts are intentional.** Sampling is with replacement, so multiple POIs in
  one ooaza are expected; each still lands on its own random interior point, and the
  `_<seq>` suffix keeps names unique.
- **Filename.** The guide spells the competitor file `competior.tsv`; the skill
  writes the corrected `competitor.tsv` (matching the `competitor_name` column and
  the `competitors` config key).
- **Config-driven ranges.** All counts, baseline/unit ranges, and the business
  duration come from `config/config.yaml`; no code change is needed to retune them.
  The sampling algorithm itself is duplicated from `synthesize-stores` so each
  skill stays self-contained.
- **Dependencies:** `pyshp` and `PyYAML`, pinned in `requirements.txt`. No
  GEOS/GDAL or other native libraries.
