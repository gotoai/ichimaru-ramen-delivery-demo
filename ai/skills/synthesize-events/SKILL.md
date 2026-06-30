---
name: synthesize-events
description: >-
  Synthesize Ichimaru's events — people-gathering activities that move local food
  demand — by population-weighted random sampling (the same Location sampling
  algorithm as synthesize-stores), driven by config/config.yaml. Per prefecture in
  synthetics/events/numbers, samples that many ooaza with probability proportional
  to 2020 Census population (with replacement — events may share an ooaza), places
  each at a uniform random point inside the sampled ooaza polygon, draws a people
  count, assigns a weighted event_date over the sales_history window (April/August/
  October weighted 5x), and writes DATA/s03_primary/event.tsv. Reproducible via
  synthetics/random_seed. Use when asked to synthesize, generate, sample, refresh,
  or bootstrap the demo's events.
---

# Synthesize events

Generates the **events** primary-data layer from real Japanese geography, reusing
the **Location sampling algorithm** of the
[`synthesize-stores`](../synthesize-stores/SKILL.md) skill: each event sits in a
大字・町 (ooaza) chosen with probability proportional to that ooaza's 2020 Census
population. As with the POI layers, events are sampled **with replacement** and
each is dropped at a **uniform random point inside the sampled ooaza polygon** (not
the centroid).

## What it produces

`DATA/s03_primary/event.tsv` — UTF-8, tab-separated, **overwritten** each run:

| column | meaning |
| --- | --- |
| `prefecture` | prefecture name in Japanese/Kanji (e.g. `東京都`) |
| `event_name` | `イベント_<都道府県名><市区町村名><大字・町名>_<seq>` |
| `latitude` / `longitude` | uniform random point inside the ooaza polygon (6 dp) |
| `people` | integer, uniform sample in `synthetics/events/people_range` |
| `event_date` | weighted random date in the `sales_history` window (see below) |

`<seq>` is a per-file, zero-padded running index that keeps every event name
unique even when an ooaza is sampled more than once (`suffix` is empty for events).

## How to run it

From the repo root, with the project `.venv` active:

```bash
source .venv/bin/activate
pip install -r requirements.txt          # first time only (pyshp, PyYAML)
python ai/skills/synthesize-events/scripts/synthesize_events.py
```

Reproducible via `synthetics/random_seed` in `config/config.yaml`. Options:
`--repo-root <path>` and `--config <path>` (both auto-detected by default).

## Inputs

- `config/config.yaml`:
  - `synthetics/random_seed`
  - `synthetics/events/numbers` — map of **Japanese prefecture name → count**
  - `synthetics/events/people_range` — `[min, max]` people per event
- `DATA/s02_intermediate/regional_population.tsv` — from `retrieve-regional-population`.
- `DATA/s02_intermediate/geoshape_<NN>/r2ka<NN>.shp` (+ `.dbf`) — from `retrieve-regional-geoshapes`.

Run `make base-data` first if those intermediate files are missing.

## Sampling algorithm, step by step

For each prefecture in `synthetics/events/numbers` (mapped Japanese name → 2-digit
JIS code):

1. **Build the pool.** Load the level-3 (大字・町) population rows, assemble each
   ooaza's polygon geometry (all 字・丁目 rings, bounding box, and centroid
   fallback), keep ooaza with population `> 0` and geometry, normalise populations
   to weights summing to `N = 10,000,000`, and build the cumulative partition.
2. **Sample `n` events with replacement.** For each, draw `u ~ Uniform[0, N)` and
   binary-search the partition for the ooaza — **no de-duplication**.
3. **Name + place.** Name = `イベント_<都道府県名><市区町村名><大字・町名>_<seq>`;
   coordinates = a **uniform random point inside the ooaza polygon**, found by
   rejection sampling in the bounding box with an even-odd point-in-polygon test
   (holes and multi-part shapes handled), falling back to the centroid only if no
   interior point is found.
4. **Draw `people`.** Uniform in `people_range`, rounded to an integer.
5. **Draw `event_date`.** A weighted random date over the `sales_history` window
   (see below).

### Event-date window & weighting (computed in JST)

The `time_horizon/sales_history` config entry is a prose rule; the skill implements
it directly:

- **end** (cutoff) = the date **two days before today** (Asia/Tokyo).
- **start** = `Jan 1 of (cutoff_year − 2)`.

Within that window each day is weighted, then one day is drawn proportional to the
weights: days in **April, August and October** carry `BOOST_WEIGHT` (5), every
other day carries 1 — so roughly five times as many events land per day in those
three months as in an ordinary month.

`CUTOFF_DAYS_BEFORE_TODAY`, `SALES_HISTORY_START_YEARS_BEFORE`, `BOOST_MONTHS` and
`BOOST_WEIGHT` constants at the top of
[scripts/synthesize_events.py](scripts/synthesize_events.py) mirror these rules;
update them there if the config/policy changes.

## Notes & maintenance

- **`numbers` keys are Japanese prefecture names** (`東京都`, …); the `prefecture`
  output column is the English display name, matching `store.tsv` and the POI files.
- **Conflicts are intentional.** Sampling is with replacement, so multiple events in
  one ooaza are expected; each still lands on its own random interior point, and the
  `_<seq>` suffix keeps names unique.
- The sampling/geometry helpers are duplicated from `synthesize-stores` /
  `synthesize-pois` so each skill stays self-contained.
- **Dependencies:** `pyshp` and `PyYAML`, pinned in `requirements.txt`. No
  GEOS/GDAL or other native libraries.
