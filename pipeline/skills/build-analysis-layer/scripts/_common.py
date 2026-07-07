"""Shared helpers for the s10_analysis builders: paths, calendar, feature metadata,
municipality rule, and spec-driven TSV writing.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from _spec import TABLES

_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

# Friendly Japanese labels + family for the 30 DFM features (mirrors the web-app's map).
FEATURE_META: dict[str, tuple[str, str]] = {
    "is_weekend": ("週末かどうか", "calendar"),
    "weekday_number": ("曜日", "calendar"),
    "month_number": ("月", "calendar"),
    "target_offdays_cos": ("季節性（周期・cos）", "calendar"),
    "target_offdays_sin": ("季節性（周期・sin）", "calendar"),
    "week-1_avg_sales": ("先週の平均売上", "lag_sales"),
    "week-1_median_sales": ("先週の中央値売上", "lag_sales"),
    "week-1_avg_weekday_sales": ("先週の平日平均売上", "lag_sales"),
    "week-1_avg_weekend_sales": ("先週の週末平均売上", "lag_sales"),
    "week-1_median_weekday_sales": ("先週の平日中央値売上", "lag_sales"),
    "week-1_median_weekend_sales": ("先週の週末中央値売上", "lag_sales"),
    "week-1to4_avg_sales": ("過去4週の平均売上", "lag_sales"),
    "week-1to4_median_sales": ("過去4週の中央値売上", "lag_sales"),
    "week-1to4_avg_weekday_sales": ("過去4週の平日平均売上", "lag_sales"),
    "week-1to4_avg_weekend_sales": ("過去4週の週末平均売上", "lag_sales"),
    "week-1to4_median_weekday_sales": ("過去4週の平日中央値売上", "lag_sales"),
    "week-1to4_median_weekend_sales": ("過去4週の週末中央値売上", "lag_sales"),
    "delta-week-1to4_avg_sales": ("直近1週と過去4週平均の差（全体）", "lag_sales"),
    "delta-week-1to4_weekday_avg_sales": ("直近1週と過去4週平均の差（平日）", "lag_sales"),
    "delta-week-1to4_weekend_avg_sales": ("直近1週と過去4週平均の差（週末）", "lag_sales"),
    "week-1to4_weekday1_avg_sales": ("過去4週の月曜平均売上", "lag_sales"),
    "week-1to4_weekday2_avg_sales": ("過去4週の火曜平均売上", "lag_sales"),
    "week-1to4_weekday3_avg_sales": ("過去4週の水曜平均売上", "lag_sales"),
    "week-1to4_weekday4_avg_sales": ("過去4週の木曜平均売上", "lag_sales"),
    "week-1to4_weekday5_avg_sales": ("過去4週の金曜平均売上", "lag_sales"),
    "week-1to4_weekday6_avg_sales": ("過去4週の土曜平均売上", "lag_sales"),
    "week-1to4_weekday7_avg_sales": ("過去4週の日曜平均売上", "lag_sales"),
    "week+1_high_temperature": ("予測日の最高気温", "weather"),
    "week+1_avg_temperature": ("予測日の平均気温", "weather"),
    "week+1_rainfall": ("予測日の降水量", "weather"),
}


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline").is_dir() and (p / "DATA").is_dir():
            return p
    raise SystemExit("Could not locate repo root (needs docs/pipeline/ and DATA/).")


def out_dir(repo_root: Path) -> Path:
    d = repo_root / "DATA" / "s10_analysis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_municipality(prefecture: str, store_name: str) -> str:
    """Store's 市区町村 (same rule as calibrate-for-weather / search-events)."""
    rest = store_name[len(prefecture):] if store_name.startswith(prefecture) else store_name
    for ch in ("市", "区"):
        i = rest.find(ch, 1)
        if i != -1:
            return rest[: i + 1]
    cands = [k for k in (rest.find("町", 1), rest.find("村", 1)) if k != -1]
    return rest[: min(cands) + 1] if cands else rest


def add_calendar(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """Add weekday_number (1=Mon..7=Sun), weekday_ja, is_weekend from an ISO date column."""
    d = pd.to_datetime(df[date_col])
    df = df.copy()
    df["weekday_number"] = d.dt.weekday + 1
    df["weekday_ja"] = d.dt.weekday.map(lambda w: _WEEKDAY_JA[w])
    df["is_weekend"] = (d.dt.weekday >= 5).astype(int)
    return df


def weekday_ja(iso: str) -> str:
    y, m, dd = (int(x) for x in iso.split("-"))
    return _WEEKDAY_JA[date(y, m, dd).weekday()]


def write_table(repo_root: Path, name: str, df: pd.DataFrame) -> Path:
    """Reindex to the spec's column order/set and write a UTF-8 TSV (empty-string nulls)."""
    cols = [c for c, _t, _n in TABLES[name]["columns"]]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SystemExit(f"{name}: builder is missing spec columns {missing}")
    df = df.reindex(columns=cols)
    path = out_dir(repo_root) / f"{name}.tsv"
    df.to_csv(path, sep="\t", index=False, na_rep="")
    print(f"  wrote {name}.tsv ({len(df)} rows)")
    return path
