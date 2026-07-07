# `analysis` — analytics serving layer design

Design for a clean, DuckDB-ready analytics layer that answers the area-manager questions
in [user_question_examples.md](user_question_examples.md). This document is the contract;
the **core** tables (dims + facts) are being built now, and the **marts** (§4) list the
analytic choices for your review before implementation.

## 1. Principles

- **Regenerated, never hand-copied.** `DATA/s10_analysis/` is a deterministic build
  artifact produced by `make analysis` from upstream stages (`s03/s06/s07/s08/s09`). It is
  git-ignored and never hand-edited, so it cannot drift from the source of truth.
- **Reshape/aggregate, never recompute canonical numbers.** The layer flattens, renames,
  joins, and aggregates. It reuses `calibrated_sales`, `residuals`, `slope`, etc. as-is; it
  does **not** re-derive `calibrated_sales` or re-run the model.
- **One clean contract for consumers.** The web-app and the AI assistant read this layer
  (not pipeline internals), insulating them from DFM feature-name / column churn.
- **DuckDB-ready.** Wide fact tables (columnar-friendly), ISO dates, ASCII snake_case
  names, one type per column. A generated `schema.sql` exposes typed views over the TSVs.

## 2. Conventions

| Aspect | Rule |
|---|---|
| Format | UTF-8 **TSV**, header row, `\n`. Canonical; DuckDB reads via `read_csv`. Parquet is a drop-in later if needed. |
| Naming | `dim_` / `fact_` / `mart_` prefixes. Columns `snake_case`, ASCII. **No units in names** (`high_temp_c`, `rain_mm`, not `最高気温(℃)`). |
| Keys | `store_name` (natural key, 80 stores), `prefecture`, `reference_date`, `target_date`, `date`. Same name **and type** across tables for clean JOINs. |
| Types | `TEXT`, `DATE` (ISO `YYYY-MM-DD`), `INTEGER`, `DOUBLE`. Booleans as `INTEGER` `0/1` (unambiguous for the CSV sniffer). |
| Nulls | Empty string in TSV → NULL in DuckDB. One type per column, always. |
| Denormalization | Facts carry `prefecture`, `weekday_ja`, `is_weekend` inline so consumers never join at read time. |
| Japanese | Kept as **labels** (`weekday_ja`, `dim_feature.label_ja`), never in column names. |
| Dictionary | Generated `SCHEMA.md` + `schema.sql` document grain/columns/types/source — also the AI's schema reference (enables future text-to-SQL). |

## 3. Core tables (dims + facts) — building now

### Dimensions

**`dim_store`** — grain: store (80 rows). *Source: `s03/store.tsv` + `matched_store_weather_station.tsv` + derived 市区町村.*

| column | type | notes |
|---|---|---|
| store_name | TEXT | natural key |
| prefecture | TEXT | |
| municipality | TEXT | 市区町村 (same rule as `calibrate-for-weather`) |
| latitude, longitude | DOUBLE | |
| weekday_baseline, weekend_baseline | DOUBLE | synthetic sales baselines |
| station_number | TEXT | matched JMA station |
| station_name | TEXT | |
| station_distance_m | DOUBLE | |

**`dim_date`** — grain: date (calendar spanning earliest actual → latest forecast target). *Derived.*

`date DATE, year INT, month INT, day INT, weekday_number INT (1=Mon…7=Sun), weekday_ja TEXT, is_weekend INT, iso_week INT`

**`dim_feature`** — grain: model feature (30 rows). *Derived (labels/families).*

`feature TEXT, label_ja TEXT, family TEXT (lag_sales | calendar | weather)`

### Facts

**`fact_forecast`** — grain: store × reference_date × target_date (1,120 rows). The primary serving table. *Source: `s09/calibrated_sales.tsv` + `calibration_info.json` (factors flattened).*

`store_name, prefecture, reference_date, target_date, weekday_number, weekday_ja, is_weekend, predicted, weather_calibrated, event_added_demand, event_count, calibrated, weather_applied, temp_gap, forecast_high_temp_c, forecast_rain_mm, bias_slope, ht_band, ht_slope, rf_band, rf_slope, self_check_ok`

**`fact_actuals`** — grain: store × date (72,960 rows). *Source: `s03/sales.tsv` enriched.*

`store_name, prefecture, date, weekday_number, weekday_ja, is_weekend, actual_sales`

**`fact_backtest`** — grain: store × reference_date × target_date (2,240 rows). Predicted vs actual with model-vs-actual weather. *Source: `s07/residuals.tsv`, JP columns ASCII-renamed.*

`store_name, prefecture, reference_date, target_date, weekday_number, weekday_ja, is_weekend, predicted, actual_sales, residual, model_high_temp_c, model_avg_temp_c, model_rain_mm, actual_high_temp_c, actual_avg_temp_c, actual_rain_mm, temp_gap`

> `residual = predicted − actual` (kept from upstream). `temp_gap = model_high_temp_c − actual_high_temp_c`.

**`fact_shap`** — grain: store × reference_date × target_date × **feature** (long, 33,600 rows). *Source: `s06/shap_values_long.tsv` + `dim_feature`.*

`store_name, prefecture, reference_date, target_date, feature, label_ja, family, feature_value, shap_value, base_value, predicted`

---

## 4. Marts (the new analytics) — **for your review before building**

These encode judgment. Please confirm/adjust the metric sets, the interval method, and the
anomaly rules.

### 4.1 `mart_store_scorecard` — grain: store (wide, 80 rows)

A per-store "at a glance" card. Proposed columns (all derivable from the core facts + `slope.tsv`):

| column | definition |
|---|---|
| **Accuracy** (from `fact_backtest`, 28 pts/store) | |
| mae_bowls | mean(\|residual\|) |
| mape_pct | mean(\|residual\| / actual_sales) × 100 |
| bias_bowls | mean(residual) — signed (+ = over-forecast) |
| bias_pct | mean(residual / actual_sales) × 100 |
| bias_direction | `over` / `under` / `neutral` (\|bias_pct\| < 3% ⇒ neutral) |
| accuracy_rank | 1 = lowest mape |
| **Diagnostic sensitivity** (from `slope.tsv`) | |
| bias_slope | per-store diagnostic slope (>1 over, <1 under) |
| rain_sensitivity | max \|net_rf_*slope − 1\| across rain bands |
| heat_sensitivity | max \|net_ht_*slope − 1\| across temp bands |
| **Demand level & trend** (from `fact_actuals` / `fact_forecast`) | |
| mean_actual_last28 | avg daily actual, last 28 days |
| demand_change_pct | (last28 − prev28) / prev28 × 100 |
| mean_calibrated_next7 | avg calibrated over upcoming window |
| demand_rank | 1 = highest mean_calibrated_next7 |
| cv_actual_last28 | std/mean of daily actuals (volatility) |
| **Events** (from `fact_forecast`) | |
| event_uplift_next7 | Σ event_added_demand over upcoming window |
| has_event_next7 | 0/1 |

**Choices to confirm:** the neutral-bias threshold (3%); the trend window (28/28 days); "upcoming window" = next 7 target dates from tomorrow (matches the web-app).

### 4.2 `mart_forecast_interval` — grain: store × target_date (wide)

Point forecasts today are single numbers; managers ask for "ブレ幅 / 欠品・余る確率 / 何杯仕込む". Proposal:

- **Method:** empirical, **de-biased residual quantiles**. From `fact_backtest`, per store, take `e = actual − predicted`; remove its median (calibration already handles bias) → dispersion sample `e′ = e − median(e)`. Interval around the calibrated point:
  `p10 = calibrated + q10(e′)`, `p50 = calibrated`, `p90 = calibrated + q90(e′)` (clamped ≥ 0).
- **Small sample fallback:** < 14 store points ⇒ pool by prefecture, then global.
- **Suggested order:** `order_sl = calibrated + q(service_level)(e′)` at a configurable service level (default **0.9**). Optional over/under-stock cost → newsvendor quantile instead.

**Choices to confirm:** empirical vs normal(σ); the de-bias step (recommended, since calibration already corrects bias — otherwise double-counted); default service level 0.9; min-sample 14 and the pooling ladder.

### 4.3 `mart_anomaly` — grain: store × signal (long)

`store_name, signal_type, severity (low/med/high), value, detail, as_of`

Proposed signals:
- **demand_shift** — recent 14-day mean vs prior 28-day baseline; flag if \|Δ%\| > 15% (med) / 30% (high).
- **persistent_bias** — share of backtest days with same-sign residual ≥ 80% ⇒ "consistently over/under-forecast".
- **accuracy_degradation** — mape rising monotonically across the 4 backtest reference dates.
- **volatility_spike** — cv_last14 > 1.5 × cv_prior.

**Choices to confirm:** thresholds (15/30%, 80%, 1.5×), windows (14/28 day), and which signals to include.

### 4.4 What-if — **not a table**

Event cancel / attendance ×2 / temp ±5℃ are computed **on demand** by a recompute
function that reuses the weather band slopes (`slope.tsv`) and the event demand rule
(`estimated_events.json`: `attendance_used × demand_probability × distance_loss`, capped).
Exposed as a web-app `/api/whatif` + chat tool. Design tracked separately.

## 5. Build & refresh

- Skill `pipeline/skills/build-analysis-layer/` → `make analysis` (runs after
  `prediction` + `diagnosis` + `calibration`).
- Emits `DATA/s10_analysis/*.tsv`, `schema.sql` (typed DuckDB views), `SCHEMA.md` (dictionary).
- Deterministic; stdlib + `pandas` where convenient. `s10_analysis/` is git-ignored.

## 6. Open decisions (summary for review)

1. Scorecard: neutral-bias threshold, trend window, upcoming-window definition.
2. Interval: empirical-vs-normal, de-bias step, default service level, pooling ladder.
3. Anomaly: thresholds, windows, signal set.
4. Whether the web-app should query **DuckDB** (SQL / future text-to-SQL) or keep reading TSVs.
