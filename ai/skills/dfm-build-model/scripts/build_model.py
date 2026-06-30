#!/usr/bin/env python3
"""Build (train, tune, evaluate) the Demand Forecast Model (DFM) for the Ichimaru demo.

Implements docs/demand-forecast/dfm_modeling.md. A LightGBM regression model
(scikit-learn API, lightgbm.LGBMRegressor) predicting week+1 daily sales.

Pipeline:
  1. Load DATA/s04_feature/{training,test}_dataset.tsv into pandas. Drop the 3 key
     columns (store_name, reference_date, target_date; store_name is NOT a
     feature), pop actual_sales as the label y, convert the rest to float so
     blanks become NaN (handled natively by LightGBM; no imputation).
  2. Grid-search max_depth x learning_rate x n_estimators (45 combos) on a single
     fixed, time-ordered 6:4 split of the training set by unique reference_date
     (earliest 60% train, latest 40% validation; no row of a reference date
     straddles the split). Select the lowest validation MAPE.
  3. Refit a fresh model with the chosen params on the FULL training set, then
     evaluate on the held-out test set.
  4. Write DATA/s05_model/: dfm_lgbm.txt (native booster text format) plus
     model_parameters.json (tuned params, seed, ordered feature-column list),
     model_validation_metrics.json, and model_test_metrics.json.

Scoring the prediction data set is out of scope (a separate skill).
The random seed comes from config/config.yaml: modeling/training/random_seed.
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_percentage_error, r2_score

KEY_COLS = ["store_name", "reference_date", "target_date"]
TARGET_COL = "actual_sales"

# Tuned by grid search (3 x 5 x 3 = 45 combinations); all other LGBMRegressor
# parameters keep their defaults. See docs/demand-forecast/dfm_modeling.md.
PARAM_GRID = {
    "max_depth": [3, 5, 7],
    "learning_rate": [0.01, 0.03, 0.1, 0.2, 0.3],
    "n_estimators": [128, 256, 512],
}

TRAIN_PORTION = 0.6  # earliest 60% of reference dates -> training portion


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "demand-forecast" / "dfm_modeling.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/demand-forecast/dfm_modeling.md not found).")


def get_cfg(d, *keys):
    """Fetch a nested config value, raising a clear error if a key is missing."""
    for k in keys:
        try:
            d = d[k]
        except (KeyError, TypeError):
            raise SystemExit(f"config.yaml: missing key '{'/'.join(map(str, keys))}'")
    return d


def load_xy(path: Path, feature_cols: list[str] | None = None):
    """Return (df, X, y, feature_cols). Drops key columns, pops actual_sales as y,
    and coerces every feature column to float (blank -> NaN). When feature_cols is
    given (e.g. for the test set), X is reindexed to that exact order so the column
    contract matches training."""
    if not path.exists():
        raise SystemExit(f"Missing input: {path} — run the dfm-create-features skill first.")
    df = pd.read_csv(path, sep="\t", dtype={c: str for c in KEY_COLS})
    if feature_cols is None:
        feature_cols = [c for c in df.columns if c not in KEY_COLS + [TARGET_COL]]
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise SystemExit(f"{path.name}: missing expected feature columns {missing}")
    X = df[feature_cols].apply(pd.to_numeric, errors="coerce").astype(float)
    y = pd.to_numeric(df[TARGET_COL], errors="coerce").astype(float)
    return df, X, y, feature_cols


def time_split_mask(df: pd.DataFrame, train_portion: float):
    """Boolean mask selecting the earliest `train_portion` of unique reference_date
    values (the training portion of the 6:4 split). The split is at reference-date
    granularity, so no reference date straddles the boundary."""
    refs = sorted(df["reference_date"].unique())
    n_train = int(round(len(refs) * train_portion))
    n_train = min(max(n_train, 1), len(refs) - 1)  # keep both sides non-empty
    train_refs = set(refs[:n_train])
    return df["reference_date"].isin(train_refs)


def evaluate(y_true, y_pred) -> dict:
    """MAPE (as a percentage), R-squared, and mean signed error (predicted-actual)."""
    return {
        "mape_percent": float(mean_absolute_percentage_error(y_true, y_pred) * 100.0),
        "r2": float(r2_score(y_true, y_pred)),
        "mean_error": float(np.mean(np.asarray(y_pred) - np.asarray(y_true))),
    }


def make_model(params: dict, seed: int) -> LGBMRegressor:
    return LGBMRegressor(random_state=seed, n_jobs=-1, verbose=-1, **params)


def scatter_actual_vs_predicted(y_true, y_pred, r2: float, out_path: Path):
    """Scatter of actual (x) vs predicted (y) on the test set, with the 45° y=x
    reference line and the R² annotated, saved as a PNG."""
    import matplotlib
    matplotlib.use("Agg")  # headless: no display needed
    import matplotlib.pyplot as plt

    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    lo = float(min(y_true.min(), y_pred.min()))
    hi = float(max(y_true.max(), y_pred.max()))

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(y_true, y_pred, s=6, alpha=0.3, edgecolors="none")
    ax.plot([lo, hi], [lo, hi], color="red", linewidth=1, label="y = x")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Actual sales")
    ax.set_ylabel("Predicted sales")
    ax.set_title("DFM actual vs. predicted (test set)")
    ax.text(0.05, 0.95, f"$R^2$ = {r2:.4f}", transform=ax.transAxes,
            va="top", ha="left",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def shap_beeswarm(model: LGBMRegressor, X: pd.DataFrame, out_path: Path):
    """Render a SHAP beeswarm summary of the model's predictions on X (the test
    set) and save it as a PNG. Imports are local so the heavy SHAP/matplotlib
    dependencies are only loaded when this step runs."""
    import matplotlib
    matplotlib.use("Agg")  # headless: no display needed
    import matplotlib.pyplot as plt
    import shap

    explainer = shap.TreeExplainer(model)
    shap_values = explainer(X)  # Explanation object carries feature names from X
    shap.plots.beeswarm(shap_values, max_display=X.shape[1], show=False)  # all features
    plt.title("DFM SHAP beeswarm (test set)")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close("all")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    feat_dir = repo / "DATA" / "s04_feature"
    out_dir = repo / "DATA" / "s05_model"
    config = yaml.safe_load((repo / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    seed = int(get_cfg(config, "modeling", "training", "random_seed"))

    # 1. Load data. Feature column order is fixed by the training header and reused
    #    for the test set (the inference contract).
    train_df, X_train, y_train, feature_cols = load_xy(feat_dir / "training_dataset.tsv")
    _, X_test, y_test, _ = load_xy(feat_dir / "test_dataset.tsv", feature_cols)

    # 2. Time-ordered 6:4 split of the training set and grid search on it.
    mask = time_split_mask(train_df, TRAIN_PORTION)
    X_tr, y_tr = X_train[mask], y_train[mask]
    X_val, y_val = X_train[~mask], y_train[~mask]
    print(f"Loaded {len(train_df)} training rows "
          f"({mask.sum()} train / {(~mask).sum()} validation by reference_date), "
          f"{len(X_test)} test rows; {len(feature_cols)} features; seed={seed}.")

    grid = [dict(zip(PARAM_GRID, combo)) for combo in product(*PARAM_GRID.values())]
    best = None
    for i, params in enumerate(grid, 1):
        model = make_model(params, seed)
        model.fit(X_tr, y_tr)
        val_metrics = evaluate(y_val, model.predict(X_val))
        if best is None or val_metrics["mape_percent"] < best["val_metrics"]["mape_percent"]:
            best = {"params": params, "val_metrics": val_metrics}
        print(f"  [{i:2d}/{len(grid)}] {params} -> "
              f"val MAPE={val_metrics['mape_percent']:.3f}%")
    print(f"Best params: {best['params']} (val MAPE={best['val_metrics']['mape_percent']:.3f}%)")

    # 3. Refit on the FULL training set with the chosen params, then test.
    final = make_model(best["params"], seed)
    final.fit(X_train, y_train)
    test_pred = final.predict(X_test)
    test_metrics = evaluate(y_test, test_pred)
    print(f"Test metrics: MAPE={test_metrics['mape_percent']:.3f}%, "
          f"R2={test_metrics['r2']:.4f}, mean_error={test_metrics['mean_error']:.3f}")

    # 4. Persist artifacts.
    out_dir.mkdir(parents=True, exist_ok=True)
    final.booster_.save_model(str(out_dir / "dfm_lgbm.txt"))

    def dump(name, obj):
        (out_dir / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")

    dump("model_parameters.json", {
        "tuned_parameters": best["params"],
        "random_seed": seed,
        "feature_columns": feature_cols,  # ordered inference contract for predict
    })
    dump("model_validation_metrics.json", best["val_metrics"])
    dump("model_test_metrics.json", test_metrics)

    # Diagnostic plots on the test set.
    scatter_actual_vs_predicted(y_test, test_pred, test_metrics["r2"],
                                out_dir / "test_scatter.png")
    shap_beeswarm(final, X_test, out_dir / "shap_beeswarm.png")
    print(f"Wrote model + metrics + test_scatter.png + shap_beeswarm.png to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
