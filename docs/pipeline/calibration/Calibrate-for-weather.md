## Calibrate for weather

Adjust the Demand Forecast Model's week+1 `predicted_sales` for the near-future
**weather forecast**. This is a *reverse-engineering* step: the diagnosis
(`docs/pipeline/diagnosis/Diagnose.md`) measured, as through-origin slopes, how the model
systematically over- or under-forecasts under a bias condition and under temperature-gap
/ rainfall conditions. Here we **undo** those known errors by dividing the prediction by
the applicable slopes, using the real forecast as the stand-in for the (still unknown)
actual weather.

The model was scored with a **previous-year weather proxy** — last year's temperature and
a rainfall that is uniformly 0 — so wherever the forecast disagrees with that proxy, the
prediction carries a known, measurable error the slopes let us remove.

### Inputs

  - `DATA/s06_prediction/predicted_sales.tsv` — the rows to calibrate:
    `prefecture`, `store_name`, `reference_date`, `target_date`, `predicted_sales`.
  - `DATA/s04_feature/predict_dataset.tsv` — the model **feature** weather, joined on
    `store_name` + `reference_date` + `target_date`: `week+1_high_temperature` (the proxy
    high temperature the model actually saw) and `week+1_rainfall` (0 by construction).
  - `DATA/s08_search/weather_forecast.tsv` — the JMA forecast, one row per
    `prefecture` + `shikuchoson` (市区町村) + `target_date`, with `最高気温` (forecast high
    temperature) and `推定日降水量(mm)` (forecast rainfall).
  - `DATA/s07_diagnosis/slope.tsv` — the diagnostic slopes: `bias_slope` (per store) and
    the pooled, net-of-baseline `net_ht_*` / `net_rf_*` weather slopes (identical across
    all rows).

### Store → forecast location

The forecast is keyed by 市区町村, which the store carries only inside its `store_name`
(`<都道府県名><市区町村名><大字・町名>店`). Reduce each store to its 市区町村 with the
**same rule as `search-events`** (`extract_location`): strip the `prefecture` prefix, then
take up to and including the first `市` (searching from index 1 so a name-initial 市原市 /
市川市 is not mis-cut); else the first `区` (東京特別区); else the first `町`/`村`. This
drops 政令市の行政区 (e.g. `千葉市中央区` → `千葉市`) and keeps 東京特別区, matching the
forecast's granularity. The forecast join key is `(prefecture, 市区町村, target_date)`.

### Condition classification

Let `Δ = feature_high_temperature − forecast_high_temperature` (model input minus the
forecast — the serve-time analogue of the diagnosis `Δht = feature − actual`). Let
`rain = forecast rainfall (mm)`; because the feature rainfall is always 0, the forecast
rainfall **is** the gap.

**Temperature band** → `net_ht_slope`, plus a `big_ht_diff` alarm flag:

  - `Δ > 5`  → band `above_5to10`, slope `net_ht_above_5to10_deg_slope`.
  - `Δ < -5` → band `below_5to10`, slope `net_ht_below_5to10_deg_slope`.
  - `|Δ| ≤ 5` → band `none`, slope factor `1.0`.

The diagnosis slopes are fitted only on the `(5, 10]` / `[-10, -5)` bands. Rather than
extrapolate a larger gap, **clamp** it to that band's slope and raise `big_ht_diff = 1`
whenever `|Δ| > 10` (the factor is being applied outside its estimated domain, so the row
is flagged for review). Otherwise `big_ht_diff = 0`.

**Rainfall band** → `net_rf_slope`:

  - `0 < rain ≤ 10`  → band `0to10`,   slope `net_rf_0to10_mm_slope`.
  - `10 < rain ≤ 30` → band `10to30`,  slope `net_rf_10to30_mm_slope`.
  - `30 < rain ≤ 80` → band `30to80`,  slope `net_rf_30to80_mm_slope`.
  - `rain > 80`      → band `above80`, slope `net_rf_above80_mm_slope`.
  - `rain == 0`      → band `none`,    slope factor `1.0`.

### Calibration formula

```
calibrated_sales = predicted_sales / bias_slope / net_ht_slope / net_rf_slope
```

  - **`bias_slope` is applied to every row**, forecast or not — it is a weather-independent
    per-store correction (the diagnosis factored the weather component out of it). If a
    store has no `bias_slope` (fewer than 7 diagnosis points), its factor is `1.0`.
  - **`net_ht_slope` and `net_rf_slope` are applied only when a forecast is available** for
    the store's 市区町村 and target date. With no forecast, both are `1.0` and only the
    bias correction remains.
  - Any factor whose band is `none`, or whose slope cell in `slope.tsv` is blank, is `1.0`
    and drops out of the division.
  - **Multiplicative composition** (dividing by all three) treats the bias, temperature and
    rainfall effects as approximately **independent** (their log-effects add). This is a
    deliberate demo assumption; each weather slope was estimated under the other's neutral
    condition, so a joint extreme (large `|Δ|` **and** heavy rain) carries a small
    interaction the product does not model.

### Output

Two files under `DATA/s09_calibration/` (created if absent).

**`weather_calibrated_sales.tsv`** — UTF-8 TSV, header row, one row per input prediction,
same order as `predicted_sales.tsv`. Floats rounded to 6 decimals; the weather columns are
blank on rows without a forecast. Columns:

  - `prefecture`, `store_name`, `reference_date`, `target_date`
  - `predicted_sales`
  - `feature_high_temperature`, `forecast_high_temperature`, `temp_gap` (`= feature − forecast`)
  - `forecast_rainfall`
  - `ht_band`, `rf_band`
  - `big_ht_diff`
  - `bias_slope`, `net_ht_slope`, `net_rf_slope` — the **applied** factors (`1.0` when the
    band is `none` or the slope cell is blank)
  - `calibrated_sales`
  - `weather_applied` — `true` when the weather factors were applied (a forecast was
    found), else `false`. Not a pass-through flag: `bias_slope` is applied either way.

**`weather_calibration_info.json`** — a JSON **list**, one object per prediction, carrying
the full-precision numbers and an auditable factor breakdown:

  - `prefecture`, `store_name`, `reference_date`, `target_date`
  - `predicted_sales`, `calibrated_sales`, `weather_applied`, `big_ht_diff`
  - `inputs` — `{feature_high_temperature, forecast_high_temperature, temp_gap,
    forecast_rainfall}`, or `null` when no forecast was found.
  - `factors` — a list of the applied factors, each
    `{type, band, slope, applied, reason}`. Always includes the `bias` factor; includes
    `temperature` / `rainfall` factors only when their band is not `none`.
  - `formula` — the literal `"calibrated = predicted / bias / net_ht / net_rf"`.
  - `self_check_ok` — `true` when `predicted / (bias · net_ht · net_rf)` reproduces
    `calibrated_sales` within `1e-6`.

### Notes

  - **Stdlib-only; deterministic** for fixed inputs.
  - **Partial coverage is expected.** The JMA forecast reaches only ~7 days out and only
    the municipalities it lists, so rows for further-out target dates or uncovered 市区町村
    get bias-only calibration (`weather_applied = false`).
  - **Upstream:** `dfm-predict-sales` (predictions), `diagnosis-calculate-slopes` (slopes),
    and `search-weather-forecast` (forecast). Run them first.
