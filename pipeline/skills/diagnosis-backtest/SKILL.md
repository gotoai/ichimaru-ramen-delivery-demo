---
name: diagnosis-backtest
description: >-
  Back-test the Demand Forecast Model (DFM) for the Ichimaru demo per
  docs/pipeline/diagnosis/Backtest.md. Re-scores the trained booster
  (DATA/s05_model/dfm_lgbm.txt) over the 4 most-recent reference dates of
  DATA/s04_feature/test_dataset.tsv — where actual_sales are known — following the
  dfm_prediction.md inference contract, joins prefecture from store.tsv, and writes
  DATA/s07_diagnosis/backtest_sales.tsv with predicted vs actual side by side.
  Use when asked to back-test, calibrate, or compare predicted vs actual sales on
  the recent test period.
---

# Back-test the demand-forecast model

Re-applies the trained DFM to the recent test period (where the true actuals are
known) and emits predicted vs actual side by side, following
[docs/pipeline/diagnosis/Backtest.md](../../../docs/pipeline/diagnosis/Backtest.md). This is the
prediction source for the residuals / diagnosis analysis
([docs/pipeline/diagnosis/Residuals.md](../../../docs/pipeline/diagnosis/Residuals.md)).

It scores the model exactly like the serve-time prediction step — read the
**Inference contract** in
[dfm_prediction.md](../../../docs/pipeline/demand-forecast/dfm_prediction.md) first.

## What it produces

One UTF-8, tab-separated file with a header row in `DATA/s07_diagnosis/`
(created if missing):

- `backtest_sales.tsv` — columns `prefecture`, `store_name`, `reference_date`,
  `target_date`, `actual_sales`, `predicted_sales`. One row per test-set row in
  the back-test window, same order as the input. `actual_sales` is the known truth
  (integer bowls) carried through from `test_dataset.tsv`; `predicted_sales` is the
  raw model output (float, no rounding or clamping).

## Back-test window

The **4 most-recent distinct `reference_date` values** in `test_dataset.tsv` — this
file is the single source of truth for the window. Downstream consumers read the
window from `backtest_sales.tsv` rather than re-deriving it.

## Inputs

- `DATA/s04_feature/test_dataset.tsv` — the test set to score, with populated
  `actual_sales` (from `dfm-create-features`).
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
python skills/diagnosis-backtest/scripts/backtest_sales.py
```

Option: `--repo-root <path>`. `make diagnosis` runs this skill, then residuals.

## How it works

- **Window.** Keeps rows whose `reference_date` is in the 4 most-recent distinct
  `reference_date` values of `test_dataset.tsv`.
- **Features.** Drops the 3 key columns and the (populated) `actual_sales`, coerces
  the rest to float (blank → `NaN`, handled natively by LightGBM) — the same input
  convention as training/prediction. `actual_sales` is never fed to the model but
  is carried through to the output.
- **Column order.** Reindexes the feature matrix to
  `model_parameters.json["feature_columns"]`, because `Booster.predict` aligns by
  position, not name. A feature-count mismatch is a hard error.
- **Predict.** Loads `dfm_lgbm.txt` as a `lgb.Booster` and predicts; output is the
  raw float (flooring/rounding is a downstream decision).
- **Prefecture.** Joined from `store.tsv` by `store_name` (the test set does not
  carry prefecture). An unmatched store is a hard error.

## Notes & maintenance

- **Third-party dependencies:** `lightgbm`, `pandas` (already in `requirements.txt`
  for the modeling skills). The key/target/output column names and the window size
  (`BACKTEST_WEEKS`) are constants at the top of
  [scripts/backtest_sales.py](scripts/backtest_sales.py).
- **Upstream dependency:** consumes the `dfm-create-features` and `dfm-build-model`
  outputs plus `store.tsv`; run those first.
- Deterministic — it only scores a fixed model against fixed inputs.
