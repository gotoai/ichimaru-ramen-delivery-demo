---
name: synthesize-stores
description: >-
  Synthesize Ichimaru's store network by population-weighted random sampling.
  For each prefecture and store count in docs/profiles/Locations.md, samples that
  many distinct 大字・町 (ooaza) with probability proportional to their 2020 Census
  population, names each store
  <prefix><prefecture><city/ward/town/village><ooaza><suffix>, places one store at
  each ooaza's polygon centroid (from the geoshape shapefiles), draws
  weekday/weekend sales baselines, and writes
  DATA/s03_primary/store.tsv. Sampling is reproducible via the
  synthetics/random_seed key in config/config.yaml. Use when asked to synthesize,
  generate, sample, refresh, or bootstrap the demo's store locations.
---

# Synthesize stores

Generates the demo's **store network** as primary data, by sampling store
locations from real Japanese geography: each store sits in a 大字・町 (ooaza)
chosen with probability proportional to that ooaza's 2020 Census population, so
stores cluster where people live.

## What it produces

`DATA/s03_primary/store.tsv` — a UTF-8, tab-separated table (header +
80 rows for the default `Locations.md`), **overwritten** on each run:

| column | meaning |
| --- | --- |
| `prefecture` | prefecture name as written in `Locations.md` (e.g. `Tokyo`) |
| `store_name` | `<prefix><prefecture><city/ward/town/village><ooaza><suffix>`, i.e. `<都道府県名><市区町村名><大字・町名>店` |
| `latitude` | ooaza polygon centroid latitude (WGS84/JGD2000, 6 dp) |
| `longitude` | ooaza polygon centroid longitude (6 dp) |
| `weekday_sale_baseline` | uniform sample in `[80, 300]` (magnitude1) |
| `weekend_sale_baseline` | uniform sample in `[50, 350]` (magnitude2) |

## How to run it

From the repo root, with the project `.venv` active:

```bash
source .venv/bin/activate
pip install -r requirements.txt          # first time only (pyshp, PyYAML)
python ai/skills/synthesize-stores/scripts/synthesize_stores.py
```

The run is **reproducible**: it reads `synthetics/random_seed` from
`config/config.yaml` and seeds a single RNG, so the same seed yields the same
store set. Change the seed (or any input) to resample. It prints one `[OK]` line
per prefecture and the total written.

Options: `--repo-root <path>` and `--config <path>` (both auto-detected by
default).

## Inputs

- `docs/profiles/Locations.md` — the prefectures and their store counts (parsed
  from `- <Prefecture>: <n> stores` lines).
- `config/config.yaml` — `synthetics/random_seed` (an integer).
- `DATA/s02_intermediate/regional_population.tsv` — produced by
  `retrieve-regional-population`.
- `DATA/s02_intermediate/geoshape_<NN>/r2ka<NN>.shp` (+ `.dbf`) — produced by
  `retrieve-regional-geoshapes`.

Run `make base-data` (the four `retrieve-*` skills) first if those intermediate
files are missing.

## Sampling algorithm, step by step

For each prefecture in `Locations.md` (mapped English → Japanese name and 2-digit
JIS code via a built-in 47-prefecture table):

1. **Load the ooaza.** From `regional_population.tsv`, keep the rows for this
   prefecture whose `地域階層レベル` is `3` (the 大字・町 level). Each carries a
   population (`総数`); suppressed (`X`) or empty (`-`) counts become `0`.

2. **Compute ooaza centroids.** Read the prefecture's shapefile with `pyshp`.
   Boundary polygons exist only at the finer 字・丁目 level, so each ooaza's
   centroid is the **area-weighted combination of its constituent 字・丁目 polygon
   centroids**, joined by `(CITY, KIHON1)` = (`市区町村コード`[2:5],
   `町丁字コード`). Polygon centroids use the signed-area shoelace formula over all
   rings, so multi-part shapes and holes combine correctly. (This reproduces the
   census `X_CODE`/`Y_CODE` representative points to ~6 dp.)

3. **Build the eligibility pool.** Keep ooaza with population `> 0` **and** a
   centroid (a geometry to place the store at).

4. **Normalise to a partition of N.** With `N = 10,000,000`, give each ooaza a
   weight `pop / Σpop · N`. Sort descending by weight and build the cumulative
   sequence — a partition of `N`.

5. **Sample `n` distinct ooaza.** Draw `u ~ Uniform[0, N)`, locate the ooaza whose
   cumulative bound contains `u` (binary search), and resample on duplicates until
   `n` distinct ooaza are chosen.

6. **Emit a store per ooaza.** Name =
   `<prefix><prefecture><city/ward/town/village><ooaza><suffix>` — i.e.
   `<都道府県名><市区町村名><大字・町名>店` (prefix `''`, suffix `店`); coordinates =
   the ooaza centroid; weekday baseline = `Uniform[80, 300]`; weekend baseline =
   `Uniform[50, 350]`.

The RNG is seeded once and prefectures are processed in `Locations.md` order, so
the whole table is a deterministic function of the inputs + seed.

## Notes & maintenance

- **Configuration lives in code constants** at the top of
  [scripts/synthesize_stores.py](scripts/synthesize_stores.py): the magnitude
  ranges (`DEFAULT_MAG1_RANGE`, `DEFAULT_MAG2_RANGE`), name affixes
  (`DEFAULT_PREFIX`/`DEFAULT_SUFFIX`), the ooaza hierarchy level (`OOAZA_LEVEL`),
  and the normalisation constant (`NORMALISED_TOTAL`). Adjust them there.
- **Adding/retargeting prefectures** needs no code change: edit `Locations.md`.
  The store count and prefecture set are read from it on every run.
- **Store names are qualified by prefecture + municipality**, so ooaza that share
  a bare name across municipalities stay distinct — e.g. 渋谷区東 and 国立市東 yield
  `東京都渋谷区東店` and `東京都国立市東店` rather than a bare `東店` twice.
- **Dependencies:** `pyshp` (pure-Python shapefile reader) and `PyYAML`, pinned in
  `requirements.txt`. No GEOS/GDAL or other native libraries are required.
- The geoshape CRS is geographic **JGD2000** (lon/lat, EPSG:4612); the `.dbf` is
  Shift_JIS, read here as CP932.
