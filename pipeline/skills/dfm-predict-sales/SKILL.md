---
name: dfm-predict-sales
description: >-
  Score the Demand Forecast Model (DFM) prediction set for the Ichimaru demo per
  docs/pipeline/demand-forecast/dfm_prediction.md. Loads the trained booster
  (DATA/s05_model/dfm_lgbm.txt), reindexes DATA/s04_feature/predict_dataset.tsv to
  the saved feature_columns order, predicts week+1 sales, joins prefecture from
  store.tsv, and writes DATA/s06_prediction/predicted_sales.tsv. Use when asked to
  predict, forecast, or score future sales with the demand-forecast model.
---

# Predict demand-forecast sales

Applies the trained DFM to the prediction data set, following
[docs/pipeline/demand-forecast/dfm_prediction.md](../../../docs/pipeline/demand-forecast/dfm_prediction.md).
This is the serve-time, feed-forward step (no training). Read that spec and the
**Inference contract** in
[dfm_modeling.md](../../../docs/pipeline/demand-forecast/dfm_modeling.md) first.

## What it produces

One UTF-8, tab-separated file with a header row in `DATA/s06_prediction/`
(created if missing):

- `predicted_sales.tsv` — columns `prefecture`, `store_name`, `reference_date`,
  `target_date`, `predicted_sales`. One row per prediction-set row;
  `predicted_sales` is the raw model output (float, no rounding or clamping).

## Inputs

- `DATA/s04_feature/predict_dataset.tsv` — the rows to score (from
  `dfm-create-features`).
- `DATA/s05_model/dfm_lgbm.txt` — the trained booster (from `dfm-build-model`).
- `DATA/s05_model/model_parameters.json` — supplies the ordered `feature_columns`
  (the inference contract).
- `DATA/s03_primary/store.tsv` — the `store_name` → `prefecture` lookup.

Run `dfm-create-features` then `dfm-build-model` (e.g. `make modeling`) first.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
pip install -r requirements.txt   # lightgbm + pandas (already used upstream)
python skills/dfm-predict-sales/scripts/predict_sales.py
```

Option: `--repo-root <path>`. `make prediction` runs this skill.

## How it works

- **Features.** Drops the 3 key columns and the blank `actual_sales`, coerces the
  rest to float (blank → `NaN`, handled natively by LightGBM) — the same input
  convention as training.
- **Column order.** Reindexes the feature matrix to
  `model_parameters.json["feature_columns"]`, because `Booster.predict` aligns by
  position, not name. A feature-count mismatch is a hard error.
- **Predict.** Loads `dfm_lgbm.txt` as a `lgb.Booster` and predicts; output is the
  raw float (flooring/rounding is a downstream decision).
- **Prefecture.** Joined from `store.tsv` by `store_name` (the prediction set does
  not carry prefecture). An unmatched store is a hard error.

## Notes & maintenance

- **Third-party dependencies:** `lightgbm`, `pandas` (already in `requirements.txt`
  for the modeling skill). The key/target/output column names are constants at the
  top of [scripts/predict_sales.py](scripts/predict_sales.py).
- **Upstream dependency:** consumes the `dfm-create-features` and `dfm-build-model`
  outputs plus `store.tsv`; run those first.
- Deterministic — it only scores a fixed model against fixed inputs.
