#!/usr/bin/env python3
"""Explain each demand-forecast prediction as per-feature SHAP contributions.

Implements docs/demand-forecast/dfm_explanation.md. Uses LightGBM's native exact
tree SHAP (booster.predict(..., pred_contrib=True)):

  1. Read DATA/s04_feature/predict_dataset.tsv. Build the feature matrix per the
     dfm_prediction.md inference contract: drop the 3 key columns and actual_sales,
     reindex to DATA/s05_model/model_parameters.json["feature_columns"] (30, in
     order), coerce to float (blank -> NaN).
  2. booster.predict(X, pred_contrib=True) -> per-feature contributions (cols
     0..29, feature_columns order) plus the base value (last col). Under the L2
     objective these are additive in bowls: predicted = base + sum(contribs).
  3. Join prefecture + predicted_sales from DATA/s06_prediction/predicted_sales.tsv
     on the 3 key columns, and self-check base + sum(contribs) ~= predicted_sales.
  4. Write DATA/s06_prediction/shap_values.tsv (wide: shap_<feature> + base_value)
     and shap_values_long.tsv (tidy: feature, feature_value, shap_value).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

KEY_COLS = ["store_name", "reference_date", "target_date"]
TARGET_COL = "actual_sales"
RECON_ATOL = 1e-3          # bowls; base + sum(contribs) vs predicted_sales


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "demand-forecast" / "dfm_explanation.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/demand-forecast/dfm_explanation.md not found).")


def require(path: Path, hint: str) -> Path:
    if not path.exists():
        raise SystemExit(f"Missing input: {path} — {hint}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    predict_tsv = repo / "DATA" / "s04_feature" / "predict_dataset.tsv"
    model_file = repo / "DATA" / "s05_model" / "dfm_lgbm.txt"
    params_file = repo / "DATA" / "s05_model" / "model_parameters.json"
    predicted_tsv = repo / "DATA" / "s06_prediction" / "predicted_sales.tsv"
    out_dir = repo / "DATA" / "s06_prediction"

    require(predict_tsv, "run the dfm-create-features skill first.")
    require(model_file, "run the dfm-build-model skill first.")
    require(params_file, "run the dfm-build-model skill first.")
    require(predicted_tsv, "run the dfm-predict-sales skill first.")

    # 1. Feature matrix in the trained column order (the inference contract).
    df = pd.read_csv(predict_tsv, sep="\t", dtype={c: str for c in KEY_COLS})
    feature_cols = json.loads(params_file.read_text(encoding="utf-8"))["feature_columns"]
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise SystemExit(f"{predict_tsv.name}: missing expected feature columns {missing}")
    X = df[feature_cols].apply(pd.to_numeric, errors="coerce").astype(float)

    # 2. Exact tree SHAP: cols 0..n-1 = per-feature contributions, last col = base.
    booster = lgb.Booster(model_file=str(model_file))
    if booster.num_feature() != len(feature_cols):
        raise SystemExit(
            f"Feature count mismatch: model expects {booster.num_feature()}, "
            f"got {len(feature_cols)} from feature_columns.")
    if list(booster.feature_name()) != list(feature_cols):
        raise SystemExit("Model feature order does not match model_parameters.json feature_columns.")
    contribs = booster.predict(X, pred_contrib=True)
    shap_vals = contribs[:, :-1]                 # (n_rows, n_features), feature_columns order
    base_value = contribs[:, -1]                 # (n_rows,), constant across rows
    recon = contribs.sum(axis=1)                 # base + sum(contribs) == booster.predict(X)

    # 3. Join prefecture + predicted_sales, then self-check the reconstruction.
    pred = pd.read_csv(predicted_tsv, sep="\t", dtype={c: str for c in KEY_COLS})
    pred = pred[[*KEY_COLS, "prefecture", "predicted_sales"]]
    df = df[KEY_COLS].merge(pred, on=KEY_COLS, how="left")
    if df[["prefecture", "predicted_sales"]].isna().any(axis=None):
        bad = df.loc[df["predicted_sales"].isna(), KEY_COLS].to_dict("records")[:5]
        raise SystemExit(
            f"{predicted_tsv.name} is missing rows for some predictions (stale?): {bad}")
    if not np.allclose(recon, df["predicted_sales"].to_numpy(float), atol=RECON_ATOL):
        worst = float(np.max(np.abs(recon - df["predicted_sales"].to_numpy(float))))
        raise SystemExit(
            f"Self-check failed: base + Σcontribs != predicted_sales (max diff {worst:.4g}). "
            "Feature order wrong or predicted_sales.tsv stale — rerun dfm-predict-sales.")

    # 4a. Wide output: shap_<feature> columns + base_value.
    shap_df = pd.DataFrame(shap_vals, columns=[f"shap_{c}" for c in feature_cols])
    wide = pd.concat(
        [df.reset_index(drop=True),
         pd.Series(base_value, name="base_value"),
         shap_df],
        axis=1)
    wide_cols = ["prefecture", *KEY_COLS, "predicted_sales", "base_value",
                 *[f"shap_{c}" for c in feature_cols]]
    out_dir.mkdir(parents=True, exist_ok=True)
    wide_path = out_dir / "shap_values.tsv"
    wide[wide_cols].to_csv(wide_path, sep="\t", index=False)

    # 4b. Tidy long output: one row per (prediction, feature).
    n_rows = len(df)
    long = pd.DataFrame({
        "prefecture": np.repeat(df["prefecture"].to_numpy(), len(feature_cols)),
        "store_name": np.repeat(df["store_name"].to_numpy(), len(feature_cols)),
        "reference_date": np.repeat(df["reference_date"].to_numpy(), len(feature_cols)),
        "target_date": np.repeat(df["target_date"].to_numpy(), len(feature_cols)),
        "predicted_sales": np.repeat(df["predicted_sales"].to_numpy(float), len(feature_cols)),
        "base_value": np.repeat(base_value, len(feature_cols)),
        "feature": np.tile(feature_cols, n_rows),
        "feature_value": X.to_numpy().reshape(-1),
        "shap_value": shap_vals.reshape(-1),
    })
    long_path = out_dir / "shap_values_long.tsv"
    long.to_csv(long_path, sep="\t", index=False)

    print(f"Wrote {n_rows} explanations ({len(feature_cols)} features each) to "
          f"{wide_path} and {long_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
