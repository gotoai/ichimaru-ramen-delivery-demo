## Residuals

Calculate residuals of recent predictions against actuals, alongside the model's
weather inputs versus the real weather, to test whether the weather-feature proxy
contributes to forecast error.

### Calculate residuals

Start from the back-test output
`DATA/s07_diagnosis/backtest_sales.tsv` (predicted
vs actual over the latest 4 test weeks — the window is defined there; do not
re-derive it here). Add a `residual` column:

```
residual = predicted_sales - actual_sales
```

`predicted_sales` is the raw model float and `actual_sales` is the integer truth,
so `residual` is fractional; a positive value means the model over-forecast. Carry
the back-test columns straight through: `prefecture`, `store_name`,
`reference_date`, `target_date`, `actual_sales`, `predicted_sales`, then `residual`.

### Check weather conditions

The hypothesis is that feeding the model last year's temperature and 0 rainfall
(the forecast-unavailable proxy) introduces input error and therefore inflates
forecast error. To test it, put the model's weather **inputs** next to the **actual**
weather on each target date. Join two sources onto the residual rows:

  - **Model feature weather** — from `DATA/s04_feature/test_dataset.tsv`, columns
    `week+1_high_temperature`, `week+1_avg_temperature`, `week+1_rainfall`, joined on
    the 3 key columns (`store_name`, `reference_date`, `target_date`). Prefix these
    with `feature_`. Note `week+1_rainfall` is **0 by construction** (the one-year
    lag carries no rainfall signal, per the model spec), so `feature_week+1_rainfall`
    is uniformly 0 — the meaningful comparison is `feature_` vs `actual_` temperature,
    plus the entirely missing actual rainfall.
  - **Actual weather** — from the weather history
    `DATA/s02_intermediate/weather_history_*.tsv` (multiple monthly files; load and
    concatenate), columns `最高気温(℃)`, `平均気温(℃)`, `降水量の合計(mm)`. Join on
    `観測地点` = the store's matched station and `日付` = `target_date` (this is the
    real weather on the target date, not the previous-year proxy). Get each store's
    station from `DATA/s03_primary/matched_store_weather_station.tsv` (the
    `match-store-weather-station` skill), whose `station_name` equals the `観測地点`
    value. That skill matches only **active, temperature-capable** stations using the
    same criteria as `create_features.py`, so for every store the `feature_` (model)
    and `actual_` temperature come from the identical station and are directly
    comparable. Date formats differ between files (`YYYY-MM-DD` vs `YYYY/M/D`), so
    normalise before joining. Prefix these with `actual_`.

### Output

Save to `DATA/s07_diagnosis/residuals.tsv` (create
`DATA/s07_diagnosis/` if it does not exist). UTF-8 TSV with a header row; one row
per back-test row. Column layout (the names below are the literal header — no
surrounding quotes):

  - prefecture
  - store_name
  - reference_date
  - target_date
  - actual_sales
  - predicted_sales
  - residual
  - feature_week+1_high_temperature
  - feature_week+1_avg_temperature
  - feature_week+1_rainfall
  - actual_最高気温(℃)
  - actual_平均気温(℃)
  - actual_降水量の合計(mm)

### Notes

  - Requires `pandas`/`numpy` (already in `requirements.txt`) — not stdlib-only.
  - Deterministic for fixed inputs.
  - Depends on the `diagnosis-backtest` and `match-store-weather-station` outputs
    plus `test_dataset.tsv` and the weather history; run those first.
