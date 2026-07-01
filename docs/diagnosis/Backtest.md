## Back Test

Back test the Demand Forecast Model (DFM) against known actuals for diagnosis
purposes: re-score the trained model over the most recent slice of the test
period, where the true `actual_sales` are available, and emit predicted vs actual
side by side. This is the prediction source consumed by the residuals /
diagnosis analysis (see `docs/diagnosis/Residuals.md`).

### Back Test Period

Use the latest 4 weeks in the test period — the **4 most-recent distinct
`reference_date` values** in `DATA/s04_feature/test_dataset.tsv`.
Keep every row whose `reference_date` is one of those 4 values. This file is the
single source of truth for the back-test window; downstream consumers (e.g.
Residuals) must read the window from the back-test output, not re-derive it.

### Model & inference contract

Use the current trained booster
`DATA/s05_model/dfm_lgbm.txt`.

Feed the model **exactly** the way the serve-time prediction step does — follow
the **Inference contract** in
[docs/demand-forecast/dfm_prediction.md](../demand-forecast/dfm_prediction.md).
In particular:

  - Drop the 3 key columns (`store_name`, `reference_date`, `target_date`) **and**
    the `actual_sales` column before scoring. Note `test_dataset.tsv` has
    `actual_sales` **populated** (unlike the blank column in `predict_dataset.tsv`),
    so it must be dropped from the feature matrix — never fed as an input.
  - Reindex the feature matrix to
    `DATA/s05_model/model_parameters.json["feature_columns"]`. `Booster.predict`
    aligns by position, not name, so this enforces the trained column order. A
    feature-count mismatch is a hard error.
  - Coerce feature values to float (blank → `NaN`, handled natively by LightGBM).

Retain each row's `actual_sales` (from `test_dataset.tsv`) unchanged and carry it
through to the output — it is not a model input, but it is what the back test is
measured against.

### Output

A UTF-8, tab-separated file with a header row and the following column layout
(one row per test-set row in the back-test window, same order as the input):

  - `prefecture` — joined from `DATA/s03_primary/store.tsv` by `store_name`
    (`test_dataset.tsv` does not carry `prefecture`). An unmatched store is a hard
    error.
  - `store_name`
  - `reference_date`
  - `target_date`
  - `actual_sales` — the known truth from `test_dataset.tsv` (integer bowls).
  - `predicted_sales` — the raw model output as a float (no rounding and no
    clamping; flooring/rounding is left to downstream consumers).

Save to `DATA/s07_diagnosis/backtest_sales.tsv`
(the `DATA/s07_diagnosis/` directory is created if it does not exist).

### Notes

  - Requires `lightgbm` + `pandas`/`numpy` (already in `requirements.txt` for the
    modeling skills) — not stdlib-only.
  - Deterministic: it only scores a fixed model against fixed inputs.
  - Depends on the `dfm-create-features`, `dfm-build-model`, and `synthesize-stores`
    outputs; run those (e.g. `make modeling`) first.
