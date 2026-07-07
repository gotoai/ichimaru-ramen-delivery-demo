# build-analysis-layer

Build the clean, **DuckDB-ready analytics serving layer** at `DATA/s10_analysis/` — a
deterministic, regenerated reshaping of the upstream pipeline outputs into a small
star-schema (dims + facts, later marts) with uniform keys, ASCII snake_case names, ISO
dates, and one type per column. It exists so the web-app and the AI assistant read one
clean, stable contract instead of pipeline internals.

Design & rationale: [`docs/analysis/s10_analysis_design.md`](../../../docs/analysis/s10_analysis_design.md).
Column contract: `scripts/_spec.py` (drives the builders **and** the generated schema/dictionary).

## Principles

- **Regenerated, never hand-copied** — always rebuilt from upstream, so it can't drift.
- **Reshape/aggregate, never recompute canonical numbers** — reuses `calibrated_sales`,
  `residuals`, `slope`, `shap_values_long`, `sales`, etc. as-is.
- **DuckDB-ready** — wide facts, typed views via generated `schema.sql`.

## Outputs (`DATA/s10_analysis/`)

Core (built now):

- `dim_store.tsv` (80) — store master + 市区町村 + matched JMA station.
- `dim_date.tsv` — calendar over the data span (weekday/is_weekend/iso_week).
- `dim_feature.tsv` (30) — model feature → Japanese label + family.
- `fact_forecast.tsv` (1,120) — the calibrated forecast chain + weather factors (primary serving table).
- `fact_actuals.tsv` (72,960) — historical daily sales + calendar.
- `fact_backtest.tsv` (2,240) — predicted vs actual with model-vs-actual weather (ASCII-renamed).
- `fact_shap.tsv` (33,600, long) — per-feature SHAP contributions with labels.
- `fact_weather_forecast.tsv` (642) — weather forecast per 市区町村/day (join to `dim_store.municipality`).
- `schema.sql` — typed DuckDB views over the TSVs.
- `SCHEMA.md` — data dictionary (also the AI's schema reference).

Marts (design doc §4):

- `mart_store_scorecard.tsv` (80) — per-store accuracy, bias, demand level/trend, events.
- `mart_forecast_interval.tsv` (1,120) — de-biased empirical prediction interval + suggested order.
- `mart_anomaly.tsv` (long) — attention list of notable per-store signals.

## Inputs

`DATA/s03_primary/{store,sales,matched_store_weather_station}.tsv`,
`s06_prediction/shap_values_long.tsv`, `s07_diagnosis/{residuals,slope}.tsv`,
`s09_calibration/{calibrated_sales.tsv,calibration_info.json}`.

## Run

```bash
cd pipeline
make analysis                    # build_core.py + build_schema.py

# Query with DuckDB (from DATA/s10_analysis/):
duckdb analysis.duckdb -c ".read schema.sql"
duckdb analysis.duckdb -c "SELECT store_name, round(avg(calibrated)) FROM fact_forecast GROUP BY 1 ORDER BY 2 DESC LIMIT 5;"
```

Uses `pandas`; deterministic for fixed inputs. `DATA/` is git-ignored (build artifact).
