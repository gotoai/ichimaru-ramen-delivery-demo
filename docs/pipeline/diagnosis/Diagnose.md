## Diagnose

Diagnose the systematic forecast-error factors related to **bias**, **temperature
proxy error**, and **rainfall**, by measuring — under each condition — how the
model's predicted sales track the actual sales.

### Input data

Read `DATA/s07_diagnosis/residuals.tsv` (the `diagnosis-calculate-residuals`
output). The columns used here are:

  - `prefecture`, `store_name`, `target_date`
  - `actual_sales`, `predicted_sales`
  - `feature_week+1_high_temperature` — the model's high-temperature input
  - `actual_最高気温(℃)` — the real high temperature on the target date
  - `actual_降水量の合計(mm)` — the real rainfall on the target date

Two derived quantities are used throughout:

  - **actual rainfall** = `actual_降水量の合計(mm)`. "No actual rainfall" means this
    value equals 0. (`feature_week+1_rainfall` is uniformly 0 by construction and is
    never used for filtering.)
  - **temperature gap** `Δht` = `feature_week+1_high_temperature` − `actual_最高気温(℃)`
    (model input minus reality). `Δht > 0` means the proxy ran hotter than reality.

### The slope metric

Every metric below is the slope of a **through-origin regression** of
`predicted_sales` on `actual_sales` over a set of data points, with actual sales on
the X axis and predicted sales on the Y axis:

```
slope = Σ(actual_sales · predicted_sales) / Σ(actual_sales²)
```

Interpretation: `slope > 1` = the model systematically **over**-forecasts under that
condition; `slope < 1` = it **under**-forecasts. **If the set has fewer than 7 data
points, the metric is skipped (recorded as an empty string).** This 7-point minimum
applies to every metric.

### Bias error metric (per store)

Computed **separately for each store**, over that store's data points with:

  - no actual rainfall, **and**
  - `|Δht| ≤ 5` (the model's temperature input is close to reality).

Record the slope as `bias_slope`. Stores with fewer than 7 such points get an empty
`bias_slope` (but still appear in the output).

### Neutral baseline metric (pooled over all stores)

The **same** condition as the bias metric — no actual rainfall **and** `|Δht| ≤ 5` —
but computed **once over the pooled data points of all stores** (not per store) and
recorded as `pooled_neutral_slope` (a single value repeated on every output row).

This is the pooled counterpart of `bias_slope`: the model's baseline over/under-forecast
when the weather proxy is ~correct. It is the **common denominator** applied to the
weather slopes below — the output reports each weather band already divided by it, as a
`net_*` column (see next sections). Because the temperature bands all hold `no rain` and
the rainfall bands all hold `|Δht| ≤ 5`, the neutral condition (`no rain` **and**
`|Δht| ≤ 5`) is their shared intersection, so one baseline serves both families.

Dividing by the baseline cancels the bias component that the pooled weather slopes
would otherwise share with `bias_slope`, so a downstream calibrator can combine the
per-store `bias_slope` with the `net_*` weather increments without double-counting bias.

`pooled_neutral_slope` itself is **kept in the output for reference** (raw, undivided).
Fewer than 7 pooled neutral points leaves it — and therefore every `net_*` column —
empty.

### High-temperature error metrics (pooled over all stores)

Computed **once over the pooled data points of all stores** (not per store), so each
metric is a single slope repeated on every store's output row. Both select points
with **no actual rainfall** and a temperature gap in the (5, 10] band, and each is
**divided by `pooled_neutral_slope`** and reported as a `net_*` column:

  - `net_ht_below_5to10_deg_slope` — the model input is **below** reality by (5, 10]
    degrees, i.e. `5 < (actual − feature) ≤ 10`  (equivalently `−10 ≤ Δht < −5`).

  - `net_ht_above_5to10_deg_slope` — the model input is **above** reality by (5, 10]
    degrees, i.e. `5 < Δht ≤ 10`.

### Rainfall error metrics (pooled over all stores)

Computed **once over the pooled data points of all stores**, each a single slope
repeated on every store's output row. All select points with `|Δht| ≤ 5` (so
temperature is controlled for) and the actual rainfall in the given band, and each is
**divided by `pooled_neutral_slope`** and reported as a `net_*` column:

  - `net_rf_0to10_mm_slope` — actual rainfall in (0, 10].
  - `net_rf_10to30_mm_slope` — actual rainfall in (10, 30].
  - `net_rf_30to80_mm_slope` — actual rainfall in (30, 80].
  - `net_rf_above80_mm_slope` — actual rainfall in (80, ∞).

A `net_*` value reads relative to the neutral baseline: `> 1` the band **adds**
over-forecast, `< 1` it **adds** under-forecast. It is empty whenever the band's raw
slope or `pooled_neutral_slope` is undefined (fewer than 7 points, or `Σactual² = 0`).

### Output

One row per store. `backtest_data_range` is the store's minimum and maximum
`target_date` (format `YYYY-MM-DD~YYYY-MM-DD`). Slopes are rounded to 6 decimals;
any metric with fewer than 7 points (or otherwise undefined) is an empty string. The
`net_*` values are the pooled band slopes divided by `pooled_neutral_slope`; the pooled
columns (`pooled_neutral_slope` and every `net_*`) are identical across all rows. Column
layout:

  - prefecture
  - store_name
  - backtest_data_range (format: `YYYY-MM-DD~YYYY-MM-DD`)
  - bias_slope
  - pooled_neutral_slope
  - net_ht_below_5to10_deg_slope
  - net_ht_above_5to10_deg_slope
  - net_rf_0to10_mm_slope
  - net_rf_10to30_mm_slope
  - net_rf_30to80_mm_slope
  - net_rf_above80_mm_slope

Write to `DATA/s07_diagnosis/slope.tsv` (UTF-8 TSV, header row).

### Notes

  - Stdlib-only; deterministic for fixed input.
  - Depends on the `diagnosis-calculate-residuals` output; run that first.
