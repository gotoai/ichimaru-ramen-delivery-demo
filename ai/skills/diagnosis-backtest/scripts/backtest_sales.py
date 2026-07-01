#!/usr/bin/env python3
"""Back-test the Demand Forecast Model (DFM) over the recent test period.

Implements docs/diagnosis/Backtest.md. Re-scores the trained model on the most
recent slice of the test set — where the true actual_sales are known — and emits
predicted vs actual side by side for diagnosis analysis:

  1. Read DATA/s04_feature/test_dataset.tsv and keep only the 4 most-recent
     distinct reference_date values (the back-test window).
  2. Build the feature matrix following the Inference contract in
     docs/demand-forecast/dfm_prediction.md: drop the 3 key columns and the
     (populated) actual_sales column, coerce the rest to float (blank -> NaN),
     and reindex to DATA/s05_model/model_parameters.json["feature_columns"]
     (Booster.predict aligns by position, so this enforces the trained order).
  3. Load DATA/s05_model/dfm_lgbm.txt as a LightGBM Booster and predict.
  4. Join store_name -> prefecture from DATA/s03_primary/store.tsv; carry the
     original actual_sales through unchanged.
  5. Write DATA/s07_diagnosis/backtest_sales.tsv (UTF-8 TSV, header row):
     prefecture, store_name, reference_date, target_date, actual_sales,
     predicted_sales (raw float, no rounding/clamping).
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
OUT_COLS = [
    "prefecture", "store_name", "reference_date", "target_date",
    "actual_sales", "predicted_sales",
]
BACKTEST_WEEKS = 4


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "diagnosis" / "Backtest.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/diagnosis/Backtest.md not found).")


def require(path: Path, hint: str) -> Path:
    if not path.exists():
        raise SystemExit(f"Missing input: {path} — {hint}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    test_tsv = repo / "DATA" / "s04_feature" / "test_dataset.tsv"
    model_file = repo / "DATA" / "s05_model" / "dfm_lgbm.txt"
    params_file = repo / "DATA" / "s05_model" / "model_parameters.json"
    store_tsv = repo / "DATA" / "s03_primary" / "store.tsv"
    out_dir = repo / "DATA" / "s07_diagnosis"

    require(test_tsv, "run the dfm-create-features skill first.")
    require(model_file, "run the dfm-build-model skill first.")
    require(params_file, "run the dfm-build-model skill first.")
    require(store_tsv, "run the synthesize-stores skill first.")

    # 1. Load the test set and keep the 4 most-recent reference_date values.
    df = pd.read_csv(test_tsv, sep="\t", dtype={c: str for c in KEY_COLS})
    ref_dates = sorted(df["reference_date"].unique())
    window = set(ref_dates[-BACKTEST_WEEKS:])
    df = df[df["reference_date"].isin(window)].reset_index(drop=True)
    if df.empty:
        raise SystemExit(f"{test_tsv.name}: no rows in the back-test window {sorted(window)}")

    # 2. Build the feature matrix in the trained column order (the contract).
    feature_cols = json.loads(params_file.read_text(encoding="utf-8"))["feature_columns"]
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise SystemExit(f"{test_tsv.name}: missing expected feature columns {missing}")
    X = df[feature_cols].apply(pd.to_numeric, errors="coerce").astype(float)

    # 3. Load the booster and predict (raw float, no rounding/clamping).
    booster = lgb.Booster(model_file=str(model_file))
    if booster.num_feature() != X.shape[1]:
        raise SystemExit(
            f"Feature count mismatch: model expects {booster.num_feature()}, "
            f"got {X.shape[1]} from feature_columns.")
    df["predicted_sales"] = booster.predict(X)

    # 4. Join prefecture by store_name; actual_sales carries through unchanged.
    if TARGET_COL not in df.columns:
        raise SystemExit(f"{test_tsv.name}: missing the {TARGET_COL} column (needed for back test).")
    store = pd.read_csv(store_tsv, sep="\t", dtype=str)[["store_name", "prefecture"]]
    out = df.merge(store, on="store_name", how="left")
    if out["prefecture"].isna().any():
        unmatched = sorted(out.loc[out["prefecture"].isna(), "store_name"].unique())
        raise SystemExit(f"No prefecture in store.tsv for stores: {unmatched}")

    # 5. Write the output TSV.
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "backtest_sales.tsv"
    out[OUT_COLS].to_csv(out_path, sep="\t", index=False)
    print(
        f"Wrote {len(out)} back-test rows over {len(window)} weeks "
        f"({', '.join(sorted(window))}) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
