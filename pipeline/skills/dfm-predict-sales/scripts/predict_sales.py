#!/usr/bin/env python3
"""Predict future (week+1) sales with the Demand Forecast Model (DFM).

Implements docs/pipeline/demand-forecast/dfm_prediction.md. The serve-time, feed-forward
step:

  1. Read DATA/s04_feature/predict_dataset.tsv. Drop the 3 key columns and the
     (blank) actual_sales column; coerce the remaining feature columns to float so
     blanks become NaN (handled natively by LightGBM).
  2. Reindex the feature matrix to DATA/s05_model/model_parameters.json
     ["feature_columns"] — Booster.predict aligns by position, so this enforces
     the training column order (the inference contract).
  3. Load DATA/s05_model/dfm_lgbm.txt as a LightGBM Booster and predict.
  4. Join store_name -> prefecture from DATA/s03_primary/store.tsv.
  5. Write DATA/s06_prediction/predicted_sales.tsv (UTF-8 TSV, header row):
     prefecture, store_name, reference_date, target_date, predicted_sales (raw
     float, no rounding/clamping).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import pandas as pd

KEY_COLS = ["store_name", "reference_date", "target_date"]
TARGET_COL = "actual_sales"
OUT_COLS = ["prefecture", "store_name", "reference_date", "target_date", "predicted_sales"]


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline" / "demand-forecast" / "dfm_prediction.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/pipeline/demand-forecast/dfm_prediction.md not found).")


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
    store_tsv = repo / "DATA" / "s03_primary" / "store.tsv"
    out_dir = repo / "DATA" / "s06_prediction"

    require(predict_tsv, "run the dfm-create-features skill first.")
    require(model_file, "run the dfm-build-model skill first.")
    require(params_file, "run the dfm-build-model skill first.")
    require(store_tsv, "run the synthesize-stores skill first.")

    # 1. Load prediction rows.
    df = pd.read_csv(predict_tsv, sep="\t", dtype={c: str for c in KEY_COLS})

    # 2. Build the feature matrix in the trained column order (the contract).
    feature_cols = json.loads(params_file.read_text(encoding="utf-8"))["feature_columns"]
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise SystemExit(f"{predict_tsv.name}: missing expected feature columns {missing}")
    X = df[feature_cols].apply(pd.to_numeric, errors="coerce").astype(float)

    # 3. Load the booster and predict (raw float, no rounding/clamping).
    booster = lgb.Booster(model_file=str(model_file))
    if booster.num_feature() != X.shape[1]:
        raise SystemExit(
            f"Feature count mismatch: model expects {booster.num_feature()}, "
            f"got {X.shape[1]} from feature_columns.")
    df["predicted_sales"] = booster.predict(X)

    # 4. Join prefecture by store_name.
    store = pd.read_csv(store_tsv, sep="\t", dtype=str)[["store_name", "prefecture"]]
    out = df.merge(store, on="store_name", how="left")
    if out["prefecture"].isna().any():
        unmatched = sorted(out.loc[out["prefecture"].isna(), "store_name"].unique())
        raise SystemExit(f"No prefecture in store.tsv for stores: {unmatched}")

    # 5. Write the output TSV.
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "predicted_sales.tsv"
    out[OUT_COLS].to_csv(out_path, sep="\t", index=False)
    print(f"Wrote {len(out)} predictions to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
