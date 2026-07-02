---
name: dfm-create-features
description: >-
  Build the Demand Forecast Model (DFM) feature data sets for the Ichimaru demo
  per docs/pipeline/demand-forecast/dfm_features.md. One row per (store_name,
  reference_date, target_date) over week+1; regression (past-zone sales
  aggregates), weather (forecast-unavailable previous-year proxy from the nearest
  station), and calendar variables, plus the actual_sales target. Writes
  DATA/s04_feature/{training,test,predict}_dataset.tsv. Use when asked to create,
  build, or refresh the demand-forecast model features / training data.
---

# Create demand-forecast features

Builds the machine-learning feature tables for demand forecasting, following
[docs/pipeline/demand-forecast/dfm_features.md](../../../docs/pipeline/demand-forecast/dfm_features.md).
Read that spec first — it is the source of truth for every column, the time-zone
definitions, and the train/test/prediction split rules.

## What it produces

Three UTF-8, tab-separated files in `DATA/s04_feature/` (created if missing).
Each row is one `(store_name, reference_date, target_date)`; `reference_date` is
a Thursday and `target_date` ranges over the 7 days of week+1.

- `training_dataset.tsv` — reference Thursdays from the first valid one up to
  immediately before the test window.
- `test_dataset.tsv` — the 8 reference Thursdays ending two weeks before today.
- `predict_dataset.tsv` — the last valid reference Thursday(s): the last two
  (this week's and the previous week's) when today is on or after Thursday, or
  just the previous week's when today is before Thursday;
  `actual_sales` is blank (the week+1 target is unknown at prediction time).

Columns (in order): the 3 key columns, 22 regression variables, 3 weather
variables, 5 calendar variables, then `actual_sales`.

## Inputs

- `DATA/s03_primary/sales.tsv` — daily store sales (the regression source and the
  `actual_sales` target).
- `DATA/s03_primary/store.tsv` — store coordinates (for the weather join).
- `DATA/s02_intermediate/weather-station.tsv` — active station coordinates.
- `DATA/s02_intermediate/weather_history_*.tsv` — daily high/avg temperature and
  rainfall.

Run the `synthesize-*` and `retrieve-weather-*` skills first (or `make base-data`
then `make synthetics`) so these exist.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/dfm-create-features/scripts/create_features.py
```

Options: `--today YYYY-MM-DD` (override the JST "current system date" that anchors
the splits — useful for testing/reproducibility), `--limit-stores N` (testing),
`--repo-root <path>`.

## How it works

- **Time zones.** For reference Thursday `R`: week-k is Monday `R-3-7k` … Sunday
  `R+3-7k`; week+1 (the target week) is Monday `R+4` … Sunday `R+10`.
- **Regression** (past zone, constant across a reference date's 7 target rows):
  week-1 and pooled week-1..4 `avg`/`median` over all / weekday / weekend days;
  `delta-week-1to4_*` = week-1 mean minus week-4 mean (`w-1_sub_w-4`); and
  `week-1to4_weekday[1-7]_avg_sales` (mean per ISO weekday across weeks 1–4).
  Empty inputs yield a blank cell.
- **Weather** (future zone, per target date). For the two temperature columns
  (`week+1_high_temperature`, `week+1_avg_temperature`) each value is a
  **previous-year proxy**: the same month/day of `target_date` one year earlier
  (the day before for a non-existent Feb 29), filled forward from up to 2 prior
  days, else blank. This proxy is applied **identically in training and
  prediction** — do not substitute the actual week+1 weather (serve-time leakage).
  The station is the nearest active station to the store by haversine distance
  (coords from `weather-station.tsv`, matched to `weather_history_*.tsv` by
  station name `観測地点`), mirroring `synthesize-sales`. `week+1_rainfall` carries
  no real signal under the one-year lag, so it is **always 0** (the no-rain
  default) rather than proxied.
- **Calendar** (future zone, per target date): `month_number`, `is_weekend`
  (ISO weekday ∈ {6,7}), `weekday_number` (Mon=1…Sun=7), and the cyclical
  `target_offdays_{cos,sin} = {cos,sin}(2π·D/366)` where `D` is days since Jan 1
  of the target year.
- **Splits** are anchored on the JST current date (or `--today`); see the spec.

## Notes & maintenance

- **No third-party dependencies** — standard library only (`csv`, `datetime`,
  `statistics`, `math`). Unlike the `synthesize-*` skills it does not read
  `pipeline/config/config.yaml`; the horizon/split rules are encoded from the spec.
- Output is deterministic for a fixed `--today` and fixed inputs.
- The weather columns, the lookback depth (`WEEKS_BACK = 4`), and the weather
  fill window are constants at the top of
  [scripts/create_features.py](scripts/create_features.py).
- **Upstream dependency:** consumes `DATA/s03_primary/{sales,store}.tsv` and the
  `weather*` intermediates; run the synthesis and weather-retrieval skills first.
