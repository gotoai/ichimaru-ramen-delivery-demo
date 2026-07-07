---
name: dfm-explain-predictions
description: >-
  Explain each demand-forecast prediction as per-feature SHAP contributions per
  docs/pipeline/demand-forecast/dfm_explanation.md. Computes exact tree SHAP with LightGBM's
  native pred_contrib on DATA/s04_feature/predict_dataset.tsv, joins prefecture and
  predicted_sales from DATA/s06_prediction/predicted_sales.tsv, and writes
  DATA/s06_prediction/shap_values.tsv (wide, shap_<feature> + base_value) and
  shap_values_long.tsv (tidy). Use when asked to explain, attribute, or find the
  driver factors behind demand-forecast predictions.
---

# Explain demand-forecast predictions

Attributes each prediction to its features as additive SHAP contributions,
following
[docs/pipeline/demand-forecast/dfm_explanation.md](../../../docs/pipeline/demand-forecast/dfm_explanation.md).
Uses LightGBM's native exact tree SHAP (`booster.predict(..., pred_contrib=True)`) —
read the **Inference contract** in
[dfm_prediction.md](../../../docs/pipeline/demand-forecast/dfm_prediction.md) first.

## What it produces

Two UTF-8, tab-separated files with a header row in `DATA/s06_prediction/` (created
if missing), keyed by the 3 key columns so they join to `predicted_sales.tsv`:

- `shap_values.tsv` — **wide**, one row per prediction: `prefecture`, `store_name`,
  `reference_date`, `target_date`, `predicted_sales`, `base_value`, then
  `shap_<feature>` for each of the 30 `feature_columns` (in order). Invariant:
  `predicted_sales = base_value + Σ shap_*`.
- `shap_values_long.tsv` — **tidy**, one row per prediction × feature (30 per
  prediction): the 4 keys, `predicted_sales`, `base_value`, `feature`,
  `feature_value`, `shap_value`. Easiest form for an agent to pull top-k drivers.

Contributions are in **bowls** and additive (the model's L2 objective). The
`shap_` prefix disambiguates contributions from the identically-named feature
*value* columns in `predict_dataset.tsv`.

## Inputs

- `DATA/s04_feature/predict_dataset.tsv` — rows to explain (from `dfm-create-features`).
- `DATA/s05_model/dfm_lgbm.txt` — the trained booster (from `dfm-build-model`).
- `DATA/s05_model/model_parameters.json` — the ordered `feature_columns`.
- `DATA/s06_prediction/predicted_sales.tsv` — `prefecture` + `predicted_sales`
  (from `dfm-predict-sales`), joined on the 3 key columns.

Run `dfm-create-features`, `dfm-build-model`, then `dfm-predict-sales` (e.g.
`make modeling` then `make prediction`) first.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
pip install -r requirements.txt   # lightgbm + pandas + numpy (already used upstream)
python skills/dfm-explain-predictions/scripts/explain_predictions.py
```

Option: `--repo-root <path>`. `make prediction` runs this skill after
`dfm-predict-sales`.

## How it works

- **Features.** Drops the 3 key columns and the blank `actual_sales`, coerces the
  rest to float (blank → `NaN`), reindexed to `feature_columns` — the same input as
  training/prediction. A feature-count or feature-order mismatch is a hard error.
- **SHAP.** `booster.predict(X, pred_contrib=True)` returns `n_features + 1` columns:
  the per-feature contributions (in `feature_columns` order) plus the base value.
  No `shap` package needed (`dfm-build-model` uses `shap` only for its beeswarm plot).
- **Join + self-check.** `prefecture` and `predicted_sales` come from
  `predicted_sales.tsv`; the skill asserts `base_value + Σ contributions ≈
  predicted_sales` per row (tolerance 1e-3 bowls) — catching a wrong feature order
  or a stale `predicted_sales.tsv`.

## Notes & maintenance

- **Third-party dependencies:** `lightgbm`, `pandas`, `numpy` (already in
  `requirements.txt`); `shap` is **not** required. Key/target column names and the
  reconstruction tolerance are constants at the top of
  [scripts/explain_predictions.py](scripts/explain_predictions.py).
- **Upstream dependency:** consumes the `dfm-create-features`, `dfm-build-model`, and
  `dfm-predict-sales` outputs; run those first.
- Deterministic — exact tree SHAP over a fixed model and fixed inputs.
