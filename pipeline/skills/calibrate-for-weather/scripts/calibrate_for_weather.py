#!/usr/bin/env python3
"""Calibrate week+1 predicted sales for the weather forecast.

Implements docs/pipeline/calibration/Calibrate-for-weather.md. For each row of
DATA/s06_prediction/predicted_sales.tsv it undoes the model's diagnosed systematic
errors:

    calibrated_sales = predicted_sales / bias_slope / net_ht_slope / net_rf_slope

- bias_slope (per store, from DATA/s07_diagnosis/slope.tsv) is applied to EVERY row.
- net_ht_slope / net_rf_slope are applied only when a JMA forecast exists for the store's
  市区町村 and target date; the temperature/rainfall band is chosen from the gap between
  the model feature weather (DATA/s04_feature/predict_dataset.tsv) and the forecast
  (DATA/s08_search/weather_forecast.tsv). A missing band or blank slope -> factor 1.0.
- Temperature gaps beyond ±10 clamp to the (5,10]/[-10,-5) band and set big_ht_diff = 1.

Writes DATA/s09_calibration/weather_calibrated_sales.tsv (one row per prediction) and
weather_calibration_info.json (a JSON list with the per-row factor breakdown).

Stdlib only; deterministic for fixed input.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# --- Band edges (keep in lock-step with Diagnose.md) -------------------------------
HT_INNER = 5.0          # |Δ| <= HT_INNER -> no temperature factor
HT_OUTER = 10.0         # |Δ| >  HT_OUTER -> clamp to band + big_ht_diff alarm
RF_EDGES = (10.0, 30.0, 80.0)   # rainfall (mm) band boundaries above 0
SELF_CHECK_TOL = 1e-6
OUT_DECIMALS = 6

# --- Input column names ------------------------------------------------------------
# predicted_sales.tsv
P_PREF, P_STORE, P_REF, P_TGT, P_PRED = (
    "prefecture", "store_name", "reference_date", "target_date", "predicted_sales")
# predict_dataset.tsv
F_STORE, F_REF, F_TGT = "store_name", "reference_date", "target_date"
F_HT, F_RAIN = "week+1_high_temperature", "week+1_rainfall"
# weather_forecast.tsv
W_PREF, W_MUNI, W_TGT = "prefecture", "shikuchoson", "target_date"
W_HT, W_RAIN = "最高気温", "推定日降水量(mm)"
# slope.tsv
S_STORE, S_BIAS = "store_name", "bias_slope"
S_HT_BELOW, S_HT_ABOVE = "net_ht_below_5to10_deg_slope", "net_ht_above_5to10_deg_slope"
S_RF = ("net_rf_0to10_mm_slope", "net_rf_10to30_mm_slope",
        "net_rf_30to80_mm_slope", "net_rf_above80_mm_slope")

OUT_COLS = [
    "prefecture", "store_name", "reference_date", "target_date",
    "predicted_sales",
    "feature_high_temperature", "forecast_high_temperature", "temp_gap",
    "forecast_rainfall",
    "ht_band", "rf_band", "big_ht_diff",
    "bias_slope", "net_ht_slope", "net_rf_slope",
    "calibrated_sales", "weather_applied",
]


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline" / "calibration" / "Calibrate-for-weather.md").exists():
            return p
    raise SystemExit(
        "Could not locate repo root (docs/pipeline/calibration/Calibrate-for-weather.md "
        "not found).")


def read_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def to_float(value):
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


def extract_municipality(prefecture: str, store_name: str) -> str:
    """The store's 市区町村, matching the forecast granularity (same rule as search-events).

    Strip the prefecture prefix, then take up to and including the first 市 (from index 1,
    so name-initial 市原市/市川市 are not mis-cut); else the first 区; else the first 町/村.
    Drops 政令市の行政区, keeps 東京特別区.
    """
    rest = store_name[len(prefecture):] if store_name.startswith(prefecture) else store_name
    for ch in ("市", "区"):
        i = rest.find(ch, 1)
        if i != -1:
            return rest[: i + 1]
    cands = [k for k in (rest.find("町", 1), rest.find("村", 1)) if k != -1]
    if cands:
        return rest[: min(cands) + 1]
    return rest          # fallback (not expected)


def classify_ht(gap, net_below, net_above):
    """(band, slope_or_None, big_ht_diff) for a temperature gap Δ = feature - forecast."""
    if gap is None:
        return "none", None, 0
    if gap > HT_INNER:
        return "above_5to10", net_above, (1 if gap > HT_OUTER else 0)
    if gap < -HT_INNER:
        return "below_5to10", net_below, (1 if gap < -HT_OUTER else 0)
    return "none", None, 0


def classify_rf(rain, net_rf):
    """(band, slope_or_None) for a forecast rainfall in mm. net_rf = the four rf slopes."""
    if rain is None or rain <= 0:
        return "none", None
    lo, mid, hi = RF_EDGES
    if rain <= lo:
        return "0to10", net_rf[0]
    if rain <= mid:
        return "10to30", net_rf[1]
    if rain <= hi:
        return "30to80", net_rf[2]
    return "above80", net_rf[3]


def fmt(value) -> str:
    """Format a float for the TSV; blank for None."""
    return "" if value is None else str(round(value, OUT_DECIMALS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    data = repo / "DATA"
    pred_path = data / "s06_prediction" / "predicted_sales.tsv"
    feat_path = data / "s04_feature" / "predict_dataset.tsv"
    fc_path = data / "s08_search" / "weather_forecast.tsv"
    slope_path = data / "s07_diagnosis" / "slope.tsv"
    for p in (pred_path, feat_path, fc_path, slope_path):
        if not p.exists():
            raise SystemExit(f"Missing input: {p}")
    out_dir = data / "s09_calibration"

    # --- slopes: per-store bias + pooled net_* (identical across rows) -------------
    slope_rows = read_tsv(slope_path)
    if not slope_rows:
        raise SystemExit(f"No rows in {slope_path}.")
    bias_of = {r[S_STORE]: to_float(r[S_BIAS]) for r in slope_rows}
    first = slope_rows[0]
    net_below = to_float(first[S_HT_BELOW])
    net_above = to_float(first[S_HT_ABOVE])
    net_rf = tuple(to_float(first[c]) for c in S_RF)

    # --- forecast: (prefecture, 市区町村, target_date) -> (high_temp, rainfall) ------
    forecast = {}
    for r in read_tsv(fc_path):
        key = (r[W_PREF], r[W_MUNI], r[W_TGT])
        forecast.setdefault(key, (to_float(r[W_HT]), to_float(r[W_RAIN])))

    # --- feature weather: (store, reference_date, target_date) -> (feat_ht, feat_rain)
    feat_of = {}
    for r in read_tsv(feat_path):
        feat_of[(r[F_STORE], r[F_REF], r[F_TGT])] = (to_float(r[F_HT]), to_float(r[F_RAIN]))

    # --- calibrate ------------------------------------------------------------------
    out_rows: list[list] = []
    info: list[dict] = []
    n_weather = n_big = 0

    for r in read_tsv(pred_path):
        pref, store = r[P_PREF], r[P_STORE]
        ref, tgt = r[P_REF], r[P_TGT]
        predicted = to_float(r[P_PRED])

        bias = bias_of.get(store)                       # applied to every row
        f_bias = bias if bias is not None else 1.0

        muni = extract_municipality(pref, store)
        fc = forecast.get((pref, muni, tgt))
        feat = feat_of.get((store, ref, tgt))
        weather_applied = fc is not None and feat is not None

        feat_ht = fc_ht = fc_rain = gap = None
        ht_band, rf_band, big = "none", "none", 0
        ht_slope = rf_slope = None
        if weather_applied:
            feat_ht, _feat_rain = feat
            fc_ht, fc_rain = fc
            if feat_ht is not None and fc_ht is not None:
                gap = feat_ht - fc_ht
            ht_band, ht_slope, big = classify_ht(gap, net_below, net_above)
            rf_band, rf_slope = classify_rf(fc_rain, net_rf)
            n_weather += 1
            n_big += big

        f_ht = ht_slope if ht_slope is not None else 1.0
        f_rf = rf_slope if rf_slope is not None else 1.0
        calibrated = predicted / f_bias / f_ht / f_rf if predicted is not None else None

        out_rows.append([
            pref, store, ref, tgt,
            fmt(predicted),
            fmt(feat_ht), fmt(fc_ht), fmt(gap),
            fmt(fc_rain),
            ht_band, rf_band, big,
            fmt(f_bias), fmt(f_ht), fmt(f_rf),
            fmt(calibrated), "true" if weather_applied else "false",
        ])

        # JSON factor breakdown -----------------------------------------------------
        factors = [{
            "type": "bias", "band": None, "slope": bias,
            "applied": bias is not None,
            "reason": ("per-store bias_slope (always applied)" if bias is not None
                       else "no bias_slope for this store (<7 diagnosis points) -> 1.0"),
        }]
        if weather_applied and ht_band != "none":
            side = "above" if ht_band == "above_5to10" else "below"
            note = " [big_ht_diff: gap beyond ±10, clamped]" if big else ""
            factors.append({
                "type": "temperature", "band": ht_band, "slope": ht_slope,
                "applied": ht_slope is not None,
                "reason": f"feature - forecast = {gap:+.1f}°C, {side} baseline{note}",
            })
        if weather_applied and rf_band != "none":
            factors.append({
                "type": "rainfall", "band": rf_band, "slope": rf_slope,
                "applied": rf_slope is not None,
                "reason": f"forecast {fc_rain:g}mm, in band {rf_band}",
            })

        check = (calibrated is not None
                 and abs(predicted / (f_bias * f_ht * f_rf) - calibrated) <= SELF_CHECK_TOL)
        info.append({
            "prefecture": pref, "store_name": store,
            "reference_date": ref, "target_date": tgt,
            "predicted_sales": predicted, "calibrated_sales": calibrated,
            "weather_applied": weather_applied, "big_ht_diff": big,
            "inputs": ({
                "feature_high_temperature": feat_ht,
                "forecast_high_temperature": fc_ht,
                "temp_gap": gap,
                "forecast_rainfall": fc_rain,
            } if weather_applied else None),
            "factors": factors,
            "formula": "calibrated = predicted / bias / net_ht / net_rf",
            "self_check_ok": check,
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = out_dir / "weather_calibrated_sales.tsv"
    json_path = out_dir / "weather_calibration_info.json"
    with open(tsv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(OUT_COLS)
        w.writerows(out_rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
        f.write("\n")

    n_bias_only = len(out_rows) - n_weather
    print(f"Wrote {tsv_path} and {json_path} ({len(out_rows)} predictions).")
    print(f"  weather-calibrated: {n_weather}  (big_ht_diff flagged: {n_big})")
    print(f"  bias-only (no forecast): {n_bias_only}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
