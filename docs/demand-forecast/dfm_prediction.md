## Prediction

Apply the Demand Forecast Model (DFM) to predict future (week+1) sales for the
rows of the prediction data set. This is the serve-time step: feed-forward only,
no training.

### Input features

Read the prediction rows from
`DATA/s04_feature/predict_dataset.tsv`. Build the
feature matrix exactly as in training (see
`docs/demand-forecast/dfm_modeling.md`, **Model
input and missing-value handling**):

  - Drop the 3 key columns (`store_name`, `reference_date`, `target_date`) and the
    (blank) `actual_sales` column.
  - Coerce the remaining feature columns to float so blanks become `NaN`, which
    LightGBM handles natively — no imputation.

**Column-order contract.** `Booster.predict` aligns inputs by **position**, so the
feature matrix must be reordered to the exact training order. Read that order from
`DATA/s05_model/model_parameters.json` →
`feature_columns` and reindex the prediction frame to it (do not rely on the TSV
header order). See dfm_modeling.md, **Inference contract (protocol rule)**.

### Use model

Load the model from
`DATA/s05_model/dfm_lgbm.txt` as a LightGBM
`Booster` (see dfm_modeling.md, **Model artifact**) and call `predict` on the
reindexed feature matrix to get one `predicted_sales` value per row.

### Prefecture lookup

`predict_dataset.tsv` does not carry `prefecture` (the feature builder drops it),
so look it up by joining `store_name` to
`DATA/s03_primary/store.tsv` (which has one row per
store with `prefecture` + `store_name`).

### Output

A UTF-8, tab-separated file with a header row and the following column layout
(one row per prediction-set row, same order as the input):

  - `prefecture`
  - `store_name`
  - `reference_date`
  - `target_date`
  - `predicted_sales` — the raw model output as a float (no rounding and no
    clamping; flooring/rounding is left to downstream consumers).

Save to `DATA/s06_prediction/predicted_sales.tsv`
(the `DATA/s06_prediction/` directory is created if it does not exist).
