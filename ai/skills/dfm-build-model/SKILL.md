---
name: dfm-build-model
description: >-
  Train, tune, and evaluate the Demand Forecast Model (DFM) for the Ichimaru demo
  per docs/demand-forecast/dfm_modeling.md. Fits a LightGBM regressor
  (scikit-learn API) on DATA/s04_feature/training_dataset.tsv with a grid search
  over max_depth/learning_rate/n_estimators on a time-ordered 6:4 split, refits
  the best params on the full training set, evaluates on test_dataset.tsv, and
  writes the booster + metrics to DATA/s05_model/. Use when asked to build, train,
  tune, or refresh the demand-forecast model. Does NOT score the prediction set.
---

# Build the demand-forecast model

Trains the LightGBM regression model for demand forecasting, following
[docs/demand-forecast/dfm_modeling.md](../../../docs/demand-forecast/dfm_modeling.md).
Read that spec first — it is the source of truth for the algorithm, the
train/validation split, the grid, the metrics, and the output contract.

Scope: this skill **trains, tunes, and evaluates** only. Scoring
`predict_dataset.tsv` to emit forecasts is a separate skill.

## What it produces

Four files in `DATA/s05_model/` (created if missing):

- `dfm_lgbm.txt` — the refit booster in LightGBM's native text format. Load with
  `lgb.Booster(model_file=...)`; `Booster.predict` aligns columns **by position**,
  so consumers must order features per `feature_columns` below.
- `model_parameters.json` — the tuned hyperparameters, the random seed, and the
  ordered `feature_columns` list (the inference contract).
- `model_validation_metrics.json` — MAPE / R² / mean error of the selected model
  on the 40% validation portion.
- `model_test_metrics.json` — the same metrics for the refit model on the test set.
- `test_scatter.png` — actual (x) vs predicted (y) scatter on the test set, with
  the 45° `y = x` line and the R² annotated.
- `shap_beeswarm.png` — a SHAP beeswarm summary of the refit model's predictions
  on the test set (per-feature impact and direction, all features).

## Inputs

- `DATA/s04_feature/training_dataset.tsv` — tuning + final fit.
- `DATA/s04_feature/test_dataset.tsv` — held-out evaluation only.
- `config/config.yaml` → `modeling/training/random_seed` — the fixed seed.

Run the `dfm-create-features` skill first (`make modeling` builds the features
then this model) so the feature TSVs exist.

## How to run it

From the repo root, with the project `.venv` active:

```bash
source .venv/bin/activate
pip install -r requirements.txt   # installs lightgbm, pandas, numpy, scikit-learn
python ai/skills/dfm-build-model/scripts/build_model.py
```

Option: `--repo-root <path>`.

## How it works

- **Input / missing values.** Loads each TSV with pandas, drops the 3 key columns
  (`store_name` is **not** a feature), pops `actual_sales` as the label `y`, and
  coerces the remaining columns to float so blanks become `NaN` (handled natively
  by LightGBM — no imputation). The training header fixes the feature column order;
  the test set is reindexed to the same order.
- **Split.** A single fixed, time-ordered **6:4** split of the training set by
  unique `reference_date` (earliest 60% train, latest 40% validation). The split
  is at reference-date granularity, so no reference date straddles the boundary
  and there is no leakage. Not k-fold CV.
- **Grid search.** 3 × 5 × 3 = 45 combinations of
  `max_depth` × `learning_rate` × `n_estimators`; all other `LGBMRegressor`
  parameters keep their defaults. The combination with the lowest **validation
  MAPE** is selected.
- **Refit.** A fresh model with the chosen params is refit on the **full** training
  set, evaluated on the test set, and saved as the production booster.
- **Metrics.** MAPE (reported as a percentage; sales are clamped ≥ 10 so no
  divide-by-zero), R², and mean signed error `(predicted − actual)` for bias.
- **Diagnostic plots** (test set, matplotlib `Agg` backend, headless): an
  actual-vs-predicted scatter with the `y = x` line and R² (`test_scatter.png`),
  and a `shap.TreeExplainer` beeswarm over all features (`shap_beeswarm.png`).
- **Determinism.** `random_state` is set from `modeling/training/random_seed`, so
  the build is reproducible for fixed inputs.

## Notes & maintenance

- **Third-party dependencies:** `lightgbm`, `pandas`, `numpy`, `scikit-learn`,
  and `shap` + `matplotlib` (for the beeswarm plot), all in `requirements.txt`.
  The grid, the `0.6` train portion, and the key/target
  column names are constants at the top of
  [scripts/build_model.py](scripts/build_model.py).
- **Upstream dependency:** consumes `DATA/s04_feature/{training,test}_dataset.tsv`;
  run `dfm-create-features` first.
- **Downstream contract:** any prediction step must reorder its feature matrix to
  `model_parameters.json["feature_columns"]` before calling `Booster.predict`.
