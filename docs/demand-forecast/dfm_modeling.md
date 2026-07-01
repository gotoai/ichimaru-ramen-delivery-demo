## Modeling

Demand Forecast Model (DFM) is a machine-learning regression model that predicts
sales in the future time zone (week+1). This document specifies how the model is
**trained, tuned, and evaluated**. Scoring the prediction data set to emit actual
forecasts is **out of scope** here and is handled by a separate skill.

### Data sets

The feature data sets are produced by the `dfm-create-features` skill. Refer to
`docs/demand-forecast/dfm_features.md` for column
definitions and the train/test/prediction split rules. This skill consumes:

  - `DATA/s04_feature/training_dataset.tsv` — used for fine-tuning and the final
    model fit.
  - `DATA/s04_feature/test_dataset.tsv` — used only for the final, held-out
    evaluation. It is never seen during tuning.

The prediction data set (`predict_dataset.tsv`) is **not** used by this skill.

### Machine-learning algorithm

Use the Python LightGBM package for model building, via its **scikit-learn API**
(`lightgbm.LGBMRegressor`). Required third-party packages (added to
`requirements.txt`):

  - `lightgbm` — the gradient-boosting model.
  - `pandas` — data-set loading and frame manipulation.
  - `numpy` — numeric arrays.
  - `scikit-learn` — evaluation metrics.

```bash
pip install lightgbm pandas numpy scikit-learn
```

### Model input and missing-value handling

Load each data set from its TSV file into a pandas DataFrame, then build the
feature matrix `X` and label vector `y` as follows:

  - **Drop the 3 key columns** (`store_name`, `reference_date`, `target_date`).
    `store_name` is **not** used as a feature; the model is store-agnostic and
    generalizes purely from the per-store sales aggregates and the weather /
    calendar variables.
  - **Pop `actual_sales` as the label `y`.** (It is the target column and is
    blank in the prediction set; this skill never reads the prediction set.)
  - **Convert all remaining feature columns to float.** Missing values become
    `NaN`, which LightGBM handles natively — no imputation is applied, as the
    most general option.

The integer-coded calendar features (`month_number`, `weekday_number`,
`is_weekend`) are kept as plain numeric values rather than declared as LightGBM
categorical features. They are always non-missing, and empirically the
categorical treatment makes no meaningful difference here; cyclicity is already
captured by `target_offdays_{cos,sin}`. (Marking them categorical remains an easy
future experiment if needed.)

### Model settings and fine-tuning

Use the `lightgbm.LGBMRegressor` class with default parameters, except the
following three, which are tuned by **grid search** (3 × 5 × 3 = 45 combinations):

  - `max_depth` ∈ [3, 5, 7]
  - `learning_rate` ∈ [0.01, 0.03, 0.1, 0.2, 0.3]
  - `n_estimators` ∈ [128, 256, 512]

**Train / validation split.** Split the training data set **along the time
horizon** by unique `reference_date` (sorted ascending) at a **6:4** ratio,
non-overlapping: the earliest 60% of reference dates form the training portion,
the latest 40% form the validation portion. The split is at reference-date
granularity — all 7 target rows of a reference date, across all stores, stay
together on the same side — so no information leaks across the boundary. This is
a single fixed split (not k-fold cross-validation).

**Grid-search loop.** For each of the 45 parameter combinations, fit on the 60%
training portion and score on the 40% validation portion. Select the combination
with the lowest validation **MAPE**.

**Performance metrics** (computed on both validation and test):

  - **MAPE** — mean absolute percentage error (sales are clamped to ≥ 10, so
    there is no divide-by-zero).
  - **R²** — coefficient of determination between actual and predicted.
  - **Mean of errors** — mean of `(predicted − actual)`, for bias checking.

**Best-model selection** uses validation MAPE.

### Reproducibility

Set the LightGBM `random_state` (and any other seed) from
`config/config.yaml`, key
`modeling/training/random_seed`, so the build is deterministic for fixed inputs.

### Final model fit

After the best hyperparameters are chosen on the 6:4 split, **refit a fresh
model with those parameters on the full training data set** (training + validation
portions combined, i.e. all of `training_dataset.tsv`). This refit model is the
one evaluated on the test set and saved as the production artifact.

### Output

All artifacts are written under `DATA/s05_model/`
(created if it does not exist).

**Model artifact.** Save the refit booster in LightGBM's native text format and
load it as a `Booster`:

```python
# save just the trained booster
model.booster_.save_model(<path-to-model-file>)   # DATA/s05_model/dfm_lgbm.txt

# load
import lightgbm as lgb
booster = lgb.Booster(model_file=<path-to-model-file>)
y_pred = booster.predict(X_new)   # numpy array; X column order must match training
```

**Inference contract (protocol rule).** `Booster.predict` aligns inputs by
**position, not by name**, so the downstream prediction skill must feed feature
columns in the exact order used at training time. To make this unambiguous, the
trained feature column order **must** be persisted as part of the saved
parameters (see `model_parameters.json` below), and any consumer must reorder its
feature matrix to match that list before calling `predict`.

**JSON sidecar files:**

  - `DATA/s05_model/model_parameters.json` — the selected (tuned) hyperparameters,
    the fixed random seed, and the **ordered feature-column list** that defines
    the inference contract.
  - `DATA/s05_model/model_validation_metrics.json` — MAPE, R², and mean error of
    the selected model on the 40% validation portion.
  - `DATA/s05_model/model_test_metrics.json` — MAPE, R², and mean error of the
    refit model on `test_dataset.tsv`.

**Diagnostic plots** (test set, matplotlib `Agg` backend, headless):

  - `DATA/s05_model/test_scatter.png` — scatter of actual (x) vs predicted (y),
    with the 45° `y = x` reference line and the R² annotated.
  - `DATA/s05_model/shap_beeswarm.png` — SHAP beeswarm summary (all features) of
    the refit model, computed with `shap.TreeExplainer`.
