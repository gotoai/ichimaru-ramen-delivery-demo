---
name: calibrate-for-weather
description: >-
  Calibrate the Demand Forecast Model's predicted sales for the near-future weather
  forecast. Compares each store's model feature weather (week+1 high temperature; rain
  is 0 by construction) against the searched JMA forecast for the store's 市区町村 and
  target date, selects the matching diagnostic slope band, and divides the prediction by
  the per-store bias slope and the net temperature/rainfall slopes from
  DATA/s07_diagnosis/slope.tsv. Writes calibrated sales as a TSV and a per-row JSON list
  of the factors and reasons. Use when you want weather-adjusted week+1 sales for
  planning, with an auditable breakdown of every adjustment.
---

# Calibrate predicted sales for weather

The model scores week+1 with a **previous-year weather proxy** (last year's temperature
and a rainfall that is uniformly 0). When a real forecast is available for a store's
location and target date, this skill corrects the prediction toward what the diagnosis
slopes say the model does wrong under that temperature-gap / rainfall condition, and
removes the store's systematic bias.

For each `(store, target_date)` prediction it:

1. joins the store's model **feature** high temperature (and the always-0 feature rain)
   from the prediction feature set;
2. joins the **forecast** high temperature and rainfall for the store's 市区町村 and that
   target date;
3. classifies the temperature gap and the rainfall into the diagnosis bands;
4. divides the prediction by the applicable slopes:

   ```
   calibrated_sales = predicted_sales / bias_slope / net_ht_slope / net_rf_slope
   ```

Any factor with no applicable band (or a blank slope) defaults to `1.0`, so it drops out
of the division. See [Diagnose.md](../../../docs/pipeline/diagnosis/Diagnose.md) for how
the slopes are defined.

**`bias_slope` is applied to every row**, whether or not a forecast exists — it is a
weather-independent, per-store correction (the pooled/weather part of the diagnosis was
factored out of it, see `Diagnose.md`). The `net_ht_slope` / `net_rf_slope` weather
factors are applied only when a forecast is available for the store's 市区町村 and target
date; otherwise they are `1.0` and only the bias correction remains. (Bias could live in
its own `calibrate-for-bias` skill, but per the requested formula it is folded in here so
one skill produces the fully calibrated number.)

## Band selection

Let `Δ = feature_high_temperature − forecast_high_temperature` (model input minus the
forecast — the serve-time stand-in for reality, matching the diagnosis `Δht`). Let
`rain = forecast rainfall (mm)` (the feature rain is always 0, so the forecast **is** the
gap).

**Temperature factor** `net_ht_slope`:

| condition on Δ | band | slope column | `big_ht_diff` |
|---|---|---|---|
| `Δ > 5` (feature above forecast) | `above_5to10` | `net_ht_above_5to10_deg_slope` | `1` if `Δ > 10`, else `0` |
| `Δ < -5` (feature below forecast) | `below_5to10` | `net_ht_below_5to10_deg_slope` | `1` if `Δ < -10`, else `0` |
| `|Δ| ≤ 5` | `none` | — (factor `1.0`) | `0` |

Note on gaps beyond ±10: there is no fitted slope past the `(5, 10]` / `[-10, -5)`
bands, so rather than extrapolate we **clamp** — a `Δ > 10` still uses the `above_5to10`
slope and a `Δ < -10` still uses the `below_5to10` slope — but raise the `big_ht_diff`
alarm flag so the row is easy to spot for review (the applied factor is being used
outside its estimated domain).

**Rainfall factor** `net_rf_slope`:

| condition on rain | band | slope column |
|---|---|---|
| `0 < rain ≤ 10` | `0to10` | `net_rf_0to10_mm_slope` |
| `10 < rain ≤ 30` | `10to30` | `net_rf_10to30_mm_slope` |
| `30 < rain ≤ 80` | `30to80` | `net_rf_30to80_mm_slope` |
| `rain > 80` | `above80` | `net_rf_above80_mm_slope` |
| `rain == 0` | `none` | — (factor `1.0`) |

The band edges are constants at the top of the script so they can be kept in lock-step
with `Diagnose.md`.

## What it produces

Written to `DATA/s09_calibration/`:

### `weather_calibrated_sales.tsv` (UTF-8 TSV, header row, one row per prediction)

- `prefecture`, `store_name`, `reference_date`, `target_date`
- `predicted_sales` — the raw model float (carried through unchanged)
- `feature_high_temperature`, `forecast_high_temperature`, `temp_gap` (`= feature − forecast`)
- `forecast_rainfall`
- `ht_band` (`below_5to10` / `above_5to10` / `none`), `rf_band` (`0to10` / … / `none`)
- `big_ht_diff` — `1` when `|temp_gap| > 10` (slope applied outside its fitted band), else `0`
- `bias_slope`, `net_ht_slope`, `net_rf_slope` — the **applied** factors (`1.0` when a
  band is `none` or the slope cell is blank)
- `calibrated_sales` — `predicted_sales / bias_slope / net_ht_slope / net_rf_slope`
- `weather_applied` — `true` when a forecast was found and the weather factors were
  applied, else `false`. Note this is **not** a pass-through flag: `bias_slope` is applied
  regardless, so a `false` row still has `calibrated_sales = predicted_sales / bias_slope`
  (equal to `predicted_sales` only when `bias_slope` is blank/1.0).

### `weather_calibration_info.json` (a JSON **list**, one object per prediction)

```json
{
  "prefecture": "千葉県",
  "store_name": "千葉県千葉市中央区新宿店",
  "reference_date": "2026-06-25",
  "target_date": "2026-07-03",
  "predicted_sales": 207.198,
  "calibrated_sales": 158.83,
  "weather_applied": true,
  "big_ht_diff": 0,
  "inputs": {
    "feature_high_temperature": 31.6,
    "forecast_high_temperature": 25.0,
    "temp_gap": 6.6,
    "forecast_rainfall": 12.0
  },
  "factors": [
    {"type": "bias", "band": null, "slope": 0.961167, "applied": true,
     "reason": "per-store bias_slope (always applied)"},
    {"type": "temperature", "band": "above_5to10", "slope": 0.940172, "applied": true,
     "reason": "feature − forecast = +6.6°C, in (5,10] above"},
    {"type": "rainfall", "band": "10to30", "slope": 1.443587, "applied": true,
     "reason": "forecast 12.0mm, in (10,30]"}
  ],
  "formula": "calibrated = predicted / bias / net_ht / net_rf",
  "self_check_ok": true
}
```

`self_check_ok` records that `predicted / (bias·net_ht·net_rf)` reproduces
`calibrated_sales` within a small tolerance. When `weather_applied` is `false`, `inputs`
is null and `factors` contains only the always-applied `bias` entry (the temperature and
rainfall factors are absent). `big_ht_diff` is `0` on such rows.

## Inputs

- `DATA/s06_prediction/predicted_sales.tsv` — `prefecture`, `store_name`,
  `reference_date`, `target_date`, `predicted_sales` (the rows to calibrate).
- `DATA/s04_feature/predict_dataset.tsv` — supplies each row's model feature weather
  (`week+1_high_temperature`, `week+1_rainfall`), joined on
  `store_name` + `reference_date` + `target_date`.
- `DATA/s08_search/weather_forecast.tsv` — the JMA forecast: `prefecture`, `shikuchoson`
  (市区町村), `target_date`, `最高気温` (high temp), `推定日降水量(mm)` (rainfall).
- `DATA/s07_diagnosis/slope.tsv` — `bias_slope` (per store) and the pooled `net_*`
  temperature/rainfall slopes.

Each store is mapped to a forecast 市区町村 with the **same reduction as `search-events`**
(`extract_location`: strip the prefecture prefix, then the first 市, else 区, else 町/村 —
dropping 政令市の行政区, keeping 東京特別区). The join key is
`(prefecture, 市区町村, target_date)`.

Run `dfm-predict-sales` (prediction), `diagnosis-calculate-slopes` (slopes), and
`search-weather-forecast` (forecast) first.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/calibrate-for-weather/scripts/calibrate_for_weather.py
```

Stdlib only (no third-party deps); deterministic for fixed inputs. Option:
`--repo-root <path>` (auto-detected by default). Re-running overwrites both outputs.

## How it works

- Loads `slope.tsv` into a per-store `bias_slope` map plus the six pooled `net_*` slopes
  (identical across stores). Blank slope cells become `1.0` at apply time.
- Loads the forecast into a `(prefecture, 市区町村, target_date) → (high_temp, rainfall)`
  map. Loads `predict_dataset.tsv` into a `(store, reference_date, target_date) →
  feature weather` map.
- Streams `predicted_sales.tsv`. For each row it applies the store's `bias_slope`
  (always), then — if a forecast is found — derives the store's 市区町村, computes `Δ` and
  the bands, resolves `net_ht_slope` / `net_rf_slope` and `big_ht_diff`, divides, and
  emits one TSV row and one JSON object.
- **Missing forecast** (no forecast row for that store+date — e.g. a target date beyond
  the forecast horizon, or a 市区町村 the forecast doesn't cover) → `weather_applied =
  false`, weather factors `1.0`, `big_ht_diff = 0`; `bias_slope` is **still** applied, so
  `calibrated_sales = predicted_sales / bias_slope`.
- Numbers are rounded to 6 decimals in the TSV; the JSON keeps full float precision for
  `*_sales` and the slopes as written in `slope.tsv`.

## Notes & maintenance

- **Stdlib-only; deterministic.** Band edges and column names are constants at the top of
  [scripts/calibrate_for_weather.py](scripts/calibrate_for_weather.py); keep them aligned
  with `Diagnose.md`.
- **Upstream dependencies:** `dfm-predict-sales`, `diagnosis-calculate-slopes`,
  `search-weather-forecast`. Run them first.

## Design notes

- **Temperature bands** use the diagnosis domains `(5, 10]` (above) and `[-10, -5)`
  (below), matching how the slopes were fit. Gaps past ±10 are **clamped** to the nearest
  band's slope and flagged with `big_ht_diff = 1` rather than dropped or extrapolated.
- **Bias is applied universally** — to every row, forecast or not — because it is a
  weather-independent per-store correction. It is folded into this skill (rather than a
  separate `calibrate-for-bias` step) so a single run yields the fully calibrated number.
- **Temperature and rainfall factors are multiplied**, which assumes their effects are
  approximately **independent** (equivalently, their log-effects add). This is a
  reasonable demo assumption. The one caveat: each slope was estimated under the other's
  neutral condition (`net_ht_*` on no-rain points, `net_rf_*` on `|Δht| ≤ 5` points), so a
  joint extreme (large temperature gap **and** heavy rain) carries some interaction the
  product can't represent — a second-order effect we accept here.
- **Coverage is partial by nature.** The JMA forecast reaches only ~7 days out and only
  the municipalities it lists, so many `predicted_sales` rows get bias-only calibration
  (`weather_applied = false`). The run prints the weather-calibrated vs bias-only split.
