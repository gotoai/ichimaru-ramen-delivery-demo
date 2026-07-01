---
name: match-store-weather-station
description: >-
  Match each Ichimaru store to its nearest usable JMA weather station and persist
  the mapping per docs/synthetics/Match-store-weather-station.md. Reproduces the
  Demand Forecast Model's own store->station assignment (nearest active,
  georeferenced, observed station by haversine distance) and writes
  DATA/s03_primary/matched_store_weather_station.tsv. Use when you need each store's
  representative weather station — e.g. to join a store's actual weather history for
  calibration/residuals analysis.
---

# Match store to weather station

Assigns each store its nearest usable weather station and writes the mapping,
following
[docs/synthetics/Match-store-weather-station.md](../../../docs/synthetics/Match-store-weather-station.md).
It reproduces the assignment the Demand Forecast Model already uses for its weather
features — computed on the fly but not persisted inside
[create_features.py](../../dfm-create-features/scripts/create_features.py) — so
downstream consumers (e.g. the residuals analysis) can join a store's *actual*
weather from the exact station the model's proxy was drawn from.

## What it produces

One UTF-8, tab-separated file with a header row in `DATA/s03_primary/` (created if
missing):

- `matched_store_weather_station.tsv` — one row per store (ordered by
  `store_name`): `prefecture`, `store_name`, `store_latitude`, `store_longitude`,
  `station_number`, `station_name`, `station_latitude`, `station_longitude`,
  `distance_m`. `station_name` equals the `観測地点` value in the weather-history
  files (the join key for actual weather).

## Assignment rule

Nearest **usable** station by haversine distance, where usable = **active**
(`End Date = 9999-99-99`) **and** **temperature-capable** (`Temperature` flag = `1`)
**and** georeferenced (`Latitude_Precipitation` / `Longitude_Precipitation`) **and**
observed (appears as `観測地点` in `weather_history_*.tsv`). Stations without a
temperature sensor (precipitation-only rain gauges) are **removed before matching**,
so every store's station can report temperature; restricting to observed stations
guarantees the matched station actually has weather rows to join.

These criteria are identical to those in `synthesize-sales` and
`dfm-create-features`, so each store's synthetic-sales weather, model feature
weather, and residual actual weather all come from the same station.

## Inputs

- `DATA/s03_primary/store.tsv` — stores with coordinates (from `synthesize-stores`).
- `DATA/s02_intermediate/weather-station.tsv` — JMA station master (from
  `retrieve-weather-station`).
- `DATA/s02_intermediate/weather_history_*.tsv` — restricts to observed stations
  (from `retrieve-weather-history`).

## How to run it

From the repo root:

```bash
python ai/skills/match-store-weather-station/scripts/match_store_weather_station.py
```

Option: `--repo-root <path>`. `make synthetics` runs this skill as its final step.

## Notes & maintenance

- **Standard library only** — no third-party dependencies. Constants (`OPEN_ENDED`,
  `STATION_COL`, `OUT_COLS`) are at the top of
  [scripts/match_store_weather_station.py](scripts/match_store_weather_station.py).
- **Kept in sync with the model:** the assignment mirrors `create_features.py`
  (`station_master` / `usable` / nearest-station). If that logic changes, update both.
- Deterministic for fixed inputs.
