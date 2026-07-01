---
name: calibration-calculate-residuals
description: >-
  Compute Demand Forecast Model back-test residuals with the model's feature weather
  vs the actual weather side by side, per docs/calibration/Residuals.md. Reads
  DATA/s07_calibration/backtest_sales.tsv (residual = predicted - actual), joins
  feature weather from test_dataset.tsv (feature_ prefix) and actual weather from the
  weather history via the store's matched station (actual_ prefix), and writes
  DATA/s07_calibration/residuals.tsv. Use to analyse forecast error and test whether
  the weather-feature proxy inflates it.
---

# Calculate back-test residuals

Computes residuals over the back-test window and lays the model's weather **inputs**
next to the **actual** weather, following
[docs/calibration/Residuals.md](../../../docs/calibration/Residuals.md). This tests
the hypothesis that the DFM's forecast-unavailable weather proxy (previous-year
temperature, 0 rainfall) contributes to forecast error.

## What it produces

One UTF-8, tab-separated file with a header row in `DATA/s07_calibration/` (created
if missing):

- `residuals.tsv` — one row per back-test row: `prefecture`, `store_name`,
  `reference_date`, `target_date`, `actual_sales`, `predicted_sales`, `residual`
  (= `predicted_sales - actual_sales`), then `feature_week+1_high_temperature`,
  `feature_week+1_avg_temperature`, `feature_week+1_rainfall`, `actual_最高気温(℃)`,
  `actual_平均気温(℃)`, `actual_降水量の合計(mm)`.

## Inputs

- `DATA/s07_calibration/backtest_sales.tsv` — predicted vs actual over the latest 4
  test weeks (from `calibration-backtest`). Defines the row set / window.
- `DATA/s04_feature/test_dataset.tsv` — the model's feature weather (from
  `dfm-create-features`).
- `DATA/s03_primary/matched_store_weather_station.tsv` — the store → weather-station
  map (from `match-store-weather-station`).
- `DATA/s02_intermediate/weather_history_*.tsv` — the actual weather (from
  `retrieve-weather-history`).

Run `calibration-backtest` and `match-store-weather-station` first.

## How to run it

From the repo root, with the project `.venv` active:

```bash
source .venv/bin/activate
pip install -r requirements.txt   # pandas (already used upstream)
python ai/skills/calibration-calculate-residuals/scripts/calculate_residuals.py
```

Option: `--repo-root <path>`. `make calibration` runs this skill after the back-test.

## How it works

- **Residual.** `predicted_sales - actual_sales` (raw float minus integer truth);
  positive = over-forecast.
- **Feature weather.** Joined from `test_dataset.tsv` on the 3 key columns
  (`store_name`, `reference_date`, `target_date`), prefixed `feature_`. Note
  `feature_week+1_rainfall` is uniformly 0 by construction (the one-year lag carries
  no rainfall signal).
- **Actual weather.** Joined from the weather history on `観測地点` = the store's
  matched `station_name` and `日付` = `target_date` (mixed date formats normalised to
  `YYYY-MM-DD`), prefixed `actual_`. Uses the same station assignment the model's
  proxy used, via `matched_store_weather_station.tsv`. Rows with no matching weather
  observation are left blank; a fully empty actual-weather join prints a warning.

## Notes & maintenance

- **Third-party dependencies:** `pandas` (already in `requirements.txt`). Column
  names and prefixes are constants at the top of
  [scripts/calculate_residuals.py](scripts/calculate_residuals.py).
- **Upstream dependency:** consumes the `calibration-backtest`,
  `match-store-weather-station`, and `dfm-create-features` outputs plus the weather
  history; run those first.
- Deterministic — a fixed join over fixed inputs.
