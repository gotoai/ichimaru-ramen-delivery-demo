#!/usr/bin/env python3
"""Build the s10_analysis CORE tables (dims + facts) — deterministic reshapes of upstream.

Reads s03/s06/s07/s09 and writes clean, DuckDB-ready TSVs to DATA/s10_analysis/. No
canonical numbers are recomputed; this only reshapes, renames, joins, and enriches.
See docs/analysis/s10_analysis_design.md and _spec.py (the column contract).

Marts (scorecard/interval/anomaly) are a separate builder, added after design sign-off.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    FEATURE_META, add_calendar, extract_municipality, find_repo_root, weekday_ja, write_table,
)

_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def build_dim_store(root: Path) -> None:
    s = pd.read_csv(root / "DATA/s03_primary/store.tsv", sep="\t")
    m = pd.read_csv(root / "DATA/s03_primary/matched_store_weather_station.tsv",
                    sep="\t", dtype={"station_number": str})
    df = s.merge(m[["store_name", "station_number", "station_name", "distance_m"]],
                 on="store_name", how="left")
    df["municipality"] = [extract_municipality(p, n) for p, n in zip(df["prefecture"], df["store_name"])]
    df = df.rename(columns={"weekday_sale_baseline": "weekday_baseline",
                            "weekend_sale_baseline": "weekend_baseline",
                            "distance_m": "station_distance_m"})
    write_table(root, "dim_store", df)


def build_dim_date(root: Path) -> None:
    dates = set()
    dates |= set(pd.read_csv(root / "DATA/s03_primary/sales.tsv", sep="\t", usecols=["date"])["date"])
    cal = pd.read_csv(root / "DATA/s09_calibration/calibrated_sales.tsv", sep="\t",
                      usecols=["reference_date", "target_date"])
    dates |= set(cal["reference_date"]) | set(cal["target_date"])
    bt = pd.read_csv(root / "DATA/s07_diagnosis/backtest_sales.tsv", sep="\t",
                     usecols=["reference_date", "target_date"])
    dates |= set(bt["reference_date"]) | set(bt["target_date"])

    rng = pd.date_range(min(dates), max(dates), freq="D")
    df = pd.DataFrame({"date": rng.strftime("%Y-%m-%d")})
    df["year"], df["month"], df["day"] = rng.year, rng.month, rng.day
    df["weekday_number"] = rng.weekday + 1
    df["weekday_ja"] = [_WEEKDAY_JA[w] for w in rng.weekday]
    df["is_weekend"] = (rng.weekday >= 5).astype(int)
    df["iso_week"] = rng.isocalendar().week.values
    write_table(root, "dim_date", df)


def build_dim_feature(root: Path) -> None:
    df = pd.DataFrame(
        [(f, lab, fam) for f, (lab, fam) in FEATURE_META.items()],
        columns=["feature", "label_ja", "family"])
    write_table(root, "dim_feature", df)


def build_fact_forecast(root: Path) -> None:
    cal = pd.read_csv(root / "DATA/s09_calibration/calibrated_sales.tsv", sep="\t",
                      dtype={"reference_date": str, "target_date": str})
    with open(root / "DATA/s09_calibration/calibration_info.json", encoding="utf-8") as fh:
        info = {(r["store_name"], r["reference_date"], r["target_date"]): r for r in json.load(fh)}

    def factors(store, ref, tgt):
        rec = info.get((store, ref, tgt))
        w = (rec or {}).get("weather", {}) or {}
        applied = bool(w.get("weather_applied"))
        inp = w.get("inputs") or {}
        fs = w.get("factors") or []
        bias = next((f["slope"] for f in fs if f["type"] == "bias"), None)
        ht = next((f for f in fs if f["type"] == "temperature"), None)
        rf = next((f for f in fs if f["type"] == "rainfall"), None)
        return {
            "weather_applied": int(applied),
            "temp_gap": inp.get("temp_gap"),
            "forecast_high_temp_c": inp.get("forecast_high_temperature"),
            "forecast_rain_mm": inp.get("forecast_rainfall"),
            "bias_slope": bias,
            "ht_band": ht["band"] if ht else ("none" if applied else ""),
            "ht_slope": ht["slope"] if ht else (1.0 if applied else None),
            "rf_band": rf["band"] if rf else ("none" if applied else ""),
            "rf_slope": rf["slope"] if rf else (1.0 if applied else None),
            "self_check_ok": int(bool((rec or {}).get("self_check_ok"))),
        }

    ext = pd.DataFrame([factors(s, r, t) for s, r, t in
                        zip(cal["store_name"], cal["reference_date"], cal["target_date"])])
    df = pd.concat([cal.reset_index(drop=True), ext], axis=1)
    df = df.rename(columns={"weather_calibrated_sales": "weather_calibrated",
                            "predicted_sales": "predicted", "calibrated_sales": "calibrated"})
    df = add_calendar(df, "target_date")
    write_table(root, "fact_forecast", df)


def build_fact_actuals(root: Path) -> None:
    df = pd.read_csv(root / "DATA/s03_primary/sales.tsv", sep="\t")
    df = df.rename(columns={"sales": "actual_sales"})
    df = add_calendar(df, "date")
    write_table(root, "fact_actuals", df)


def build_fact_backtest(root: Path) -> None:
    df = pd.read_csv(root / "DATA/s07_diagnosis/residuals.tsv", sep="\t")
    df = df.rename(columns={
        "actual_sales": "actual_sales",
        "predicted_sales": "predicted",
        "feature_week+1_high_temperature": "model_high_temp_c",
        "feature_week+1_avg_temperature": "model_avg_temp_c",
        "feature_week+1_rainfall": "model_rain_mm",
        "actual_最高気温(℃)": "actual_high_temp_c",
        "actual_平均気温(℃)": "actual_avg_temp_c",
        "actual_降水量の合計(mm)": "actual_rain_mm",
    })
    df["temp_gap"] = df["model_high_temp_c"] - df["actual_high_temp_c"]
    df = add_calendar(df, "target_date")
    write_table(root, "fact_backtest", df)


def build_fact_shap(root: Path) -> None:
    df = pd.read_csv(root / "DATA/s06_prediction/shap_values_long.tsv", sep="\t",
                     dtype={"reference_date": str, "target_date": str})
    df = df.rename(columns={"predicted_sales": "predicted"})
    df["label_ja"] = df["feature"].map(lambda f: FEATURE_META.get(f, (f, ""))[0])
    df["family"] = df["feature"].map(lambda f: FEATURE_META.get(f, ("", ""))[1])
    write_table(root, "fact_shap", df)


def build_fact_weather_forecast(root: Path) -> None:
    wf = pd.read_csv(root / "DATA/s08_search/weather_forecast.tsv", sep="\t",
                     dtype=str).fillna("")
    df = pd.DataFrame({
        "prefecture": wf["prefecture"], "municipality": wf["shikuchoson"],
        "sub_region": wf["sub_region"], "target_date": wf["target_date"],
        "summary": wf["天気概況"],
    })
    df["precip_prob_pct"] = pd.to_numeric(wf["降水確率(%)"], errors="coerce").astype("Int64")
    df["high_temp_c"] = pd.to_numeric(wf["最高気温"], errors="coerce").astype("Int64")
    df["low_temp_c"] = pd.to_numeric(wf["最低気温"], errors="coerce").astype("Int64")
    df["rain_mm"] = pd.to_numeric(wf["推定日降水量(mm)"], errors="coerce")
    df = add_calendar(df, "target_date")
    write_table(root, "fact_weather_forecast", df)


def main() -> int:
    root = find_repo_root(Path(__file__).resolve())
    print(f"Building s10_analysis core tables under {root / 'DATA/s10_analysis'}")
    build_dim_store(root)
    build_dim_date(root)
    build_dim_feature(root)
    build_fact_forecast(root)
    build_fact_actuals(root)
    build_fact_backtest(root)
    build_fact_shap(root)
    build_fact_weather_forecast(root)
    print("Core layer done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
