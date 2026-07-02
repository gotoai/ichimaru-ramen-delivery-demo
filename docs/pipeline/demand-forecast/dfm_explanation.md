## Explanation

Explain the driver factors behind each demand-forecast prediction, as per-feature
SHAP contributions that exactly reconstruct the predicted value.

### Inputs

  - `DATA/s04_feature/predict_dataset.tsv` — the rows to explain (from
    `dfm-create-features`). Carries the 3 key columns, the feature columns, and a
    blank `actual_sales`.
  - `DATA/s05_model/dfm_lgbm.txt` — the trained LightGBM booster (from
    `dfm-build-model`).
  - `DATA/s05_model/model_parameters.json` — supplies the ordered `feature_columns`
    (the inference contract — the 30 features, in trained order).
  - `DATA/s06_prediction/predicted_sales.tsv` — supplies `prefecture` and
    `predicted_sales` (from `dfm-predict-sales`), joined on the 3 key columns
    (`store_name`, `reference_date`, `target_date`). `predict_dataset.tsv` carries
    neither.

### Feature matrix (inference contract)

Build the feature matrix exactly as the prediction step does — follow the
**Inference contract** in [dfm_prediction.md](dfm_prediction.md): drop the 3 key
columns and `actual_sales`, reindex to
`model_parameters.json["feature_columns"]` (the 30 features, in that order — SHAP
must run in the trained column order or the attributions are wrong), and coerce the
values to float so blanks become `NaN` (handled natively by LightGBM).

### SHAP contributions

Compute exact tree SHAP values with LightGBM's native
`booster.predict(X, pred_contrib=True)`. This returns `n_features + 1` columns: the
first 30 are the per-feature contributions (in `feature_columns` order) and the last
is the **base value** (the model's expected output). No extra dependency is needed
(`dfm-build-model` uses the `shap` package only for its beeswarm plot).

Semantics: under the model's L2 (regression) objective the contributions are in
**bowls** and additive, so for every row

```
predicted_sales  ≈  base_value  +  Σ (per-feature SHAP contributions)
```

**Self-check:** assert `base_value + Σ contributions ≈ predicted_sales` (joined from
`predicted_sales.tsv`) for every row, within a small tolerance; a mismatch means the
feature matrix is out of order or `predicted_sales.tsv` is stale — fail loudly.

### Outputs

Two UTF-8, tab-separated files with a header row in `DATA/s06_prediction/` (created
if missing), one/-set per prediction row, same order as the input, keyed by the 3
key columns so they join cleanly to `predicted_sales.tsv`.

**Wide — `shap_values.tsv`** (one row per prediction):

  - `prefecture`
  - `store_name`
  - `reference_date`
  - `target_date`
  - `predicted_sales` — raw model float (no rounding/clamping), from
    `predicted_sales.tsv`.
  - `base_value` — the model's expected output (same for every row).
  - `shap_<feature>` for each of the 30 `feature_columns`, in that order — the
    per-feature SHAP contribution (in bowls). The `shap_` prefix disambiguates these
    contributions from the identically-named feature *value* columns in
    `predict_dataset.tsv`.

  Invariant: `predicted_sales = base_value + Σ shap_*`.

**Tidy long — `shap_values_long.tsv`** (one row per prediction × feature, 30 rows per
prediction) for easy top-k driver extraction by an agent:

  - `prefecture`, `store_name`, `reference_date`, `target_date`
  - `predicted_sales`, `base_value`
  - `feature` — the feature name
  - `feature_value` — the feature's value for this row (float; blank if `NaN`)
  - `shap_value` — that feature's SHAP contribution (in bowls)

### Notes

  - Third-party dependencies: `lightgbm`, `pandas`, `numpy` (already in
    `requirements.txt`); the `shap` package is **not** required (native
    `pred_contrib`).
  - Deterministic — exact tree SHAP over a fixed model and fixed inputs.
  - Depends on the `dfm-create-features`, `dfm-build-model`, and `dfm-predict-sales`
    outputs; run those (e.g. `make modeling` then `make prediction`) first.
