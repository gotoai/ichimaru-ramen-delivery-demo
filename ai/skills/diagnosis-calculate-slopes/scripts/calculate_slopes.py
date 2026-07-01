#!/usr/bin/env python3
"""Diagnose systematic forecast-error factors as through-origin slopes.

Implements docs/diagnosis/Diagnose.md. Reads the back-test residuals and, for
several weather/bias conditions, measures how predicted sales track actual sales via
the slope of a through-origin regression of predicted (Y) on actual (X):

    slope = Σ(actual · predicted) / Σ(actual²)

slope > 1 => the model over-forecasts under that condition; < 1 => under-forecasts.
A metric computed from fewer than 7 points is left blank.

Metrics:
  - bias_slope                 per store; no actual rain and |Δht| <= 5.
  - ht_below_5to10_deg_slope   pooled; no actual rain and 5 < (actual-feature) <= 10.
  - ht_above_5to10_deg_slope   pooled; no actual rain and 5 < (feature-actual) <= 10.
  - rf_0to10 / 10to30 / 30to80 / above80  pooled; |Δht| <= 5 and actual rain in band.

where Δht = feature_week+1_high_temperature - actual_最高気温(℃) (model minus reality).
The pooled (all-store) metrics are identical across every store's output row.

Reads  DATA/s07_diagnosis/residuals.tsv
Writes DATA/s07_diagnosis/slope.tsv   (UTF-8 TSV, header row, one row per store)

Stdlib only; deterministic for fixed input.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

MIN_POINTS = 7          # a slope over fewer points than this is left blank
SLOPE_DECIMALS = 6

# residuals.tsv columns used here
C_PREF = "prefecture"
C_STORE = "store_name"
C_TARGET = "target_date"
C_ACTUAL_SALES = "actual_sales"
C_PRED_SALES = "predicted_sales"
C_FEAT_HT = "feature_week+1_high_temperature"
C_ACTUAL_HT = "actual_最高気温(℃)"
C_ACTUAL_RAIN = "actual_降水量の合計(mm)"

OUT_COLS = [
    "prefecture", "store_name", "backtest_data_range",
    "bias_slope",
    "ht_below_5to10_deg_slope", "ht_above_5to10_deg_slope",
    "rf_0to10_mm_slope", "rf_10to30_mm_slope",
    "rf_30to80_mm_slope", "rf_above80_mm_slope",
]


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "diagnosis" / "Diagnose.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/diagnosis/Diagnose.md not found).")


def to_float(value: str):
    """Parse a numeric cell; blank / non-numeric -> None."""
    if value is None:
        return None
    v = value.strip()
    if v == "" or v.lower() == "nan":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def slope_through_origin(points) -> str:
    """Through-origin OLS slope of Y on X for (x, y) pairs, formatted or blank.

    Blank ("") when there are fewer than MIN_POINTS points or Σx² is 0.
    """
    if len(points) < MIN_POINTS:
        return ""
    sxx = sum(x * x for x, _ in points)
    if sxx == 0:
        return ""
    sxy = sum(x * y for x, y in points)
    return str(round(sxy / sxx, SLOPE_DECIMALS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    src = repo_root / "DATA" / "s07_diagnosis" / "residuals.tsv"
    if not src.exists():
        raise SystemExit(
            f"Missing input: {src} — run the diagnosis-calculate-residuals skill first.")
    dest = repo_root / "DATA" / "s07_diagnosis" / "slope.tsv"

    with open(src, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if not rows:
        raise SystemExit(f"No rows in {src}.")

    # Per-store accumulators (order = first appearance in the file).
    stores: list[str] = []
    pref_of: dict[str, str] = {}
    dates_of: dict[str, list[str]] = {}
    bias_pts: dict[str, list] = {}

    # Pooled (all-store) accumulators, one list of (actual, predicted) per metric.
    pooled = {k: [] for k in (
        "ht_below_5to10_deg_slope", "ht_above_5to10_deg_slope",
        "rf_0to10_mm_slope", "rf_10to30_mm_slope",
        "rf_30to80_mm_slope", "rf_above80_mm_slope",
    )}

    for r in rows:
        store = r[C_STORE]
        if store not in pref_of:
            stores.append(store)
            pref_of[store] = r[C_PREF]
            dates_of[store] = []
            bias_pts[store] = []
        if r[C_TARGET]:
            dates_of[store].append(r[C_TARGET])

        a_sales = to_float(r[C_ACTUAL_SALES])
        p_sales = to_float(r[C_PRED_SALES])
        rain = to_float(r[C_ACTUAL_RAIN])
        feat_ht = to_float(r[C_FEAT_HT])
        act_ht = to_float(r[C_ACTUAL_HT])
        # Every metric needs the sales pair and the weather fields it filters on.
        if a_sales is None or p_sales is None:
            continue
        pt = (a_sales, p_sales)

        # Bias and temperature metrics require actual rainfall and the temperature gap.
        if rain is not None and feat_ht is not None and act_ht is not None:
            dht = feat_ht - act_ht
            if rain == 0 and abs(dht) <= 5:
                bias_pts[store].append(pt)
            if rain == 0 and 5 < (act_ht - feat_ht) <= 10:
                pooled["ht_below_5to10_deg_slope"].append(pt)
            if rain == 0 and 5 < (feat_ht - act_ht) <= 10:
                pooled["ht_above_5to10_deg_slope"].append(pt)
            if abs(dht) <= 5:
                if 0 < rain <= 10:
                    pooled["rf_0to10_mm_slope"].append(pt)
                elif 10 < rain <= 30:
                    pooled["rf_10to30_mm_slope"].append(pt)
                elif 30 < rain <= 80:
                    pooled["rf_30to80_mm_slope"].append(pt)
                elif rain > 80:
                    pooled["rf_above80_mm_slope"].append(pt)

    pooled_slopes = {name: slope_through_origin(pts) for name, pts in pooled.items()}

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(OUT_COLS)
        for store in stores:
            dts = dates_of[store]
            date_range = f"{min(dts)}~{max(dts)}" if dts else ""
            w.writerow([
                pref_of[store], store, date_range,
                slope_through_origin(bias_pts[store]),
                pooled_slopes["ht_below_5to10_deg_slope"],
                pooled_slopes["ht_above_5to10_deg_slope"],
                pooled_slopes["rf_0to10_mm_slope"],
                pooled_slopes["rf_10to30_mm_slope"],
                pooled_slopes["rf_30to80_mm_slope"],
                pooled_slopes["rf_above80_mm_slope"],
            ])

    n_bias = sum(1 for s in stores if slope_through_origin(bias_pts[s]) != "")
    print(f"Wrote {dest} ({len(stores)} stores; {n_bias} with a bias_slope).")
    print("Pooled slopes: " + ", ".join(
        f"{k}={v or '—'}" for k, v in pooled_slopes.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
