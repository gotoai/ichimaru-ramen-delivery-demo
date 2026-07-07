---
name: diagnosis-calculate-slopes
description: >-
  Diagnose systematic Demand Forecast Model error factors (bias, temperature-proxy
  error, rainfall) as through-origin slopes of predicted vs actual sales, per
  docs/pipeline/diagnosis/Diagnose.md. Reads DATA/s07_diagnosis/residuals.tsv and writes
  DATA/s07_diagnosis/slope.tsv with a per-store bias_slope plus a pooled neutral
  baseline and pooled temperature/rainfall slopes. Use to quantify whether the weather-feature proxy or
  rainfall drives forecast error, and in which direction (>1 over-forecast, <1 under).
---

# Calculate diagnostic slopes

Measures, under several weather/bias conditions, how the model's predicted sales
track the actual sales, following
[docs/pipeline/diagnosis/Diagnose.md](../../../docs/pipeline/diagnosis/Diagnose.md). Each metric is
the slope of a **through-origin regression** of `predicted_sales` (Y) on
`actual_sales` (X):

```
slope = Σ(actual · predicted) / Σ(actual²)
```

`slope > 1` means the model systematically **over**-forecasts under that condition;
`slope < 1` means it **under**-forecasts. A metric computed from fewer than **7**
data points is left blank.

## What it produces

One UTF-8, tab-separated file with a header row, `DATA/s07_diagnosis/slope.tsv`,
**one row per store**:

- `prefecture`, `store_name`
- `backtest_data_range` — the store's min~max `target_date` (`YYYY-MM-DD~YYYY-MM-DD`).
- `bias_slope` — **per store**, over that store's points with no actual rainfall and
  a small temperature gap (`|Δht| ≤ 5`).
- `pooled_neutral_slope` — **pooled over all stores**, the same condition as
  `bias_slope` (no actual rainfall and `|Δht| ≤ 5`). Reported **raw, for reference**;
  it is the baseline denominator applied to the `net_*` columns below.
- `net_ht_below_5to10_deg_slope`, `net_ht_above_5to10_deg_slope` — **pooled over all
  stores**, no actual rainfall, model temperature input below / above reality by
  (5, 10]°, **divided by `pooled_neutral_slope`**.
- `net_rf_0to10_mm_slope`, `net_rf_10to30_mm_slope`, `net_rf_30to80_mm_slope`,
  `net_rf_above80_mm_slope` — **pooled over all stores**, `|Δht| ≤ 5`, actual rainfall
  in (0,10] / (10,30] / (30,80] / (80,∞) mm, **divided by `pooled_neutral_slope`**.

Each `net_*` is the band's marginal effect relative to the neutral baseline (`> 1` adds
over-forecast, `< 1` adds under-forecast); dividing out the baseline cancels the shared
bias, so a calibrator can combine `bias_slope` with the `net_*` increments without
double-counting bias. A `net_*` cell is blank when its raw band slope or
`pooled_neutral_slope` is undefined (< 7 points).

The seven pooled columns (`pooled_neutral_slope` + the six `net_*`) are identical on
every row; only `bias_slope` (and the store identity / date range) vary by store.

Here `Δht = feature_week+1_high_temperature − actual_最高気温(℃)` (model input minus
reality) and "actual rainfall" is `actual_降水量の合計(mm)` (the feature rainfall is 0
by construction and is never used).

## Inputs

- `DATA/s07_diagnosis/residuals.tsv` — from `diagnosis-calculate-residuals`.
  Provides `actual_sales`, `predicted_sales`, `feature_week+1_high_temperature`,
  `actual_最高気温(℃)`, and `actual_降水量の合計(mm)`.

Run `diagnosis-calculate-residuals` first.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/diagnosis-calculate-slopes/scripts/calculate_slopes.py
```

The script is dependency-free (Python standard library only). Option:
`--repo-root <path>` (auto-detected by default). Re-running overwrites the output.
`make diagnosis` runs this skill after the residuals step.

## How it works

- Streams `residuals.tsv` once. For every row it parses the sales pair and the
  weather fields; rows missing a needed value are excluded from the affected metric
  only (not the whole store).
- **Per-store bias + pooled neutral baseline:** points with `actual rain == 0` and
  `|Δht| ≤ 5` are accumulated per store (`bias_slope`) and, in parallel, once across
  all stores (`pooled_neutral_slope`); each is their through-origin slope (blank if
  < 7 points).
- **Pooled temperature/rainfall:** points are accumulated across all stores into the
  six bucket lists (the rainfall buckets are mutually exclusive and all require
  `|Δht| ≤ 5`; the temperature buckets require `actual rain == 0`). Each bucket's
  through-origin slope is then **divided by `pooled_neutral_slope`** and written as its
  `net_*` column (one value repeated on every output row).
- Raw slopes and net ratios are rounded to 6 decimals; any metric with < 7 points (or
  `Σactual² = 0`) — or a `net_*` whose baseline is undefined — is an empty string.

## Notes & maintenance

- **Stdlib-only; deterministic** — a fixed computation over fixed input.
- **Upstream dependency:** consumes the `diagnosis-calculate-residuals` output; run
  it first. Column names, the 7-point minimum, and the bucket edges are constants at
  the top of [scripts/calculate_slopes.py](scripts/calculate_slopes.py).
- Because most weather buckets are sparse **per store**, the temperature/rainfall
  metrics are pooled across all stores so each has enough points to be meaningful;
  only `bias_slope` (the densest condition) is resolved per store.
