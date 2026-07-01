#!/usr/bin/env python3
"""Compute back-test residuals with model-feature vs actual weather side by side.

Implements docs/diagnosis/Residuals.md. Tests whether the DFM's weather-feature
proxy (previous-year temperature, 0 rainfall) contributes to forecast error:

  1. Read DATA/s07_diagnosis/backtest_sales.tsv (predicted vs actual over the
     latest 4 test weeks) and add residual = predicted_sales - actual_sales.
  2. Join the model's feature weather from DATA/s04_feature/test_dataset.tsv
     (week+1_high_temperature, week+1_avg_temperature, week+1_rainfall) on the 3 key
     columns, prefixed feature_.
  3. Join the actual weather from DATA/s02_intermediate/weather_history_*.tsv
     (最高気温(℃), 平均気温(℃), 降水量の合計(mm)) on the store's matched station
     (観測地点) and the target date (日付), prefixed actual_. The store->station map
     comes from DATA/s03_primary/matched_store_weather_station.tsv (the same
     assignment the model's proxy used).
  4. Write DATA/s07_diagnosis/residuals.tsv (UTF-8 TSV, header row).
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import pandas as pd

KEY_COLS = ["store_name", "reference_date", "target_date"]

FEATURE_WEATHER = ["week+1_high_temperature", "week+1_avg_temperature", "week+1_rainfall"]
ACTUAL_WEATHER = ["最高気温(℃)", "平均気温(℃)", "降水量の合計(mm)"]
STATION_COL = "観測地点"
DATE_COL = "日付"

OUT_COLS = (
    ["prefecture", "store_name", "reference_date", "target_date",
     "actual_sales", "predicted_sales", "residual"]
    + [f"feature_{c}" for c in FEATURE_WEATHER]
    + [f"actual_{c}" for c in ACTUAL_WEATHER]
)


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "diagnosis" / "Residuals.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/diagnosis/Residuals.md not found).")


def require(path: Path, hint: str) -> Path:
    if not path.exists():
        raise SystemExit(f"Missing input: {path} — {hint}")
    return path


def iso(series: pd.Series) -> pd.Series:
    """Normalise mixed date formats (YYYY-MM-DD, YYYY/M/D) to YYYY-MM-DD strings."""
    return pd.to_datetime(series, format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")


def load_actual_weather(inter: Path, stations: set, dates: set) -> pd.DataFrame:
    """Actual weather for the matched stations/target dates, keyed (station, date)."""
    frames = []
    for path in sorted(glob.glob(str(inter / "weather_history_*.tsv"))):
        df = pd.read_csv(path, sep="\t", usecols=[STATION_COL, DATE_COL, *ACTUAL_WEATHER],
                         dtype={STATION_COL: str, DATE_COL: str})
        df["__date"] = iso(df[DATE_COL])
        df = df[df[STATION_COL].isin(stations) & df["__date"].isin(dates)]
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=[STATION_COL, "__date", *ACTUAL_WEATHER])
    out = pd.concat(frames, ignore_index=True)
    # One row per (station, date); keep the last observation if any duplicates.
    return out.drop_duplicates([STATION_COL, "__date"], keep="last")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    backtest_tsv = repo / "DATA" / "s07_diagnosis" / "backtest_sales.tsv"
    test_tsv = repo / "DATA" / "s04_feature" / "test_dataset.tsv"
    matched_tsv = repo / "DATA" / "s03_primary" / "matched_store_weather_station.tsv"
    inter = repo / "DATA" / "s02_intermediate"
    out_path = repo / "DATA" / "s07_diagnosis" / "residuals.tsv"

    require(backtest_tsv, "run the diagnosis-backtest skill first.")
    require(test_tsv, "run the dfm-create-features skill first.")
    require(matched_tsv, "run the match-store-weather-station skill first.")

    # 1. Residuals from the back test.
    df = pd.read_csv(backtest_tsv, sep="\t", dtype={c: str for c in KEY_COLS})
    df["residual"] = df["predicted_sales"] - df["actual_sales"]

    # 2. Model feature weather, prefixed feature_.
    feat = pd.read_csv(test_tsv, sep="\t", dtype={c: str for c in KEY_COLS},
                       usecols=[*KEY_COLS, *FEATURE_WEATHER])
    feat = feat.rename(columns={c: f"feature_{c}" for c in FEATURE_WEATHER})
    df = df.merge(feat, on=KEY_COLS, how="left")

    # 3. Actual weather, via the store's matched station, prefixed actual_.
    matched = pd.read_csv(matched_tsv, sep="\t", dtype=str)[["store_name", "station_name"]]
    df = df.merge(matched, on="store_name", how="left")
    if df["station_name"].isna().any():
        missing = sorted(df.loc[df["station_name"].isna(), "store_name"].unique())
        raise SystemExit(f"No matched station for stores: {missing}")

    df["__date"] = iso(df["target_date"])
    weather = load_actual_weather(inter, set(df["station_name"]), set(df["__date"]))
    df = df.merge(
        weather.rename(columns={STATION_COL: "station_name", "__date": "__date"}),
        on=["station_name", "__date"], how="left")
    df = df.rename(columns={c: f"actual_{c}" for c in ACTUAL_WEATHER})

    matched_actual = df[f"actual_{ACTUAL_WEATHER[0]}"].notna().sum()
    if matched_actual == 0:
        print("WARNING: no actual weather matched — check station names / target-date coverage.",
              file=sys.stderr)

    # 4. Write the output.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df[OUT_COLS].to_csv(out_path, sep="\t", index=False)
    print(f"Wrote {len(df)} residual rows ({matched_actual} with actual weather) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
