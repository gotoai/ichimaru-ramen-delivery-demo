#!/usr/bin/env python3
"""Build Demand Forecast Model (DFM) feature data sets for the Ichimaru demo.

Implements docs/demand-forecast/dfm_features.md. The row granularity is one row
per (store_name, reference_date, target_date), where reference_date is a Thursday
and target_date ranges over the 7 days of week+1 (the next week). Features:

  * Regression  — past-zone sales aggregates (week-1 and week-1..4 levels,
    week-1 minus week-4 deltas, and per-ISO-weekday means).
  * Weather     — high/avg temperature for the target date, taken from a
    forecast-unavailable proxy (previous-year same month/day of the nearest
    weather station), applied identically in training and prediction. Rainfall
    (week+1_rainfall) carries no real signal under the one-year lag, so it always
    falls back to the no-rain default (0).
  * Calendar    — month, weekday, weekend flag, and a cyclical day-of-year code.

Target: actual_sales (the `sales` column of DATA/s03_primary/sales.tsv), left
missing for the prediction set.

Outputs three UTF-8 TSVs in DATA/s04_feature/: training_dataset.tsv,
test_dataset.tsv, predict_dataset.tsv. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import math
import statistics
import sys
from pathlib import Path

EARTH_RADIUS_M = 6_371_000.0
JST = dt.timezone(dt.timedelta(hours=9))
OPEN_ENDED = "9999-99-99"

# Weather columns (proxy date -> value) and the join keys, per dfm_features.md.
HIGH_TEMP_COL = "最高気温(℃)"
AVG_TEMP_COL = "平均気温(℃)"
RAIN_COL = "降水量の合計(mm)"
STATION_COL = "観測地点"
DATE_COL = "日付"
WEATHER_FILL_DAYS = 2          # fill from up to 2 preceding days
WEEKS_BACK = 4                 # week-1 .. week-4 lookback


# --- geometry ------------------------------------------------------------------
def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# --- data loading --------------------------------------------------------------
def read_tsv(path: Path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, AttributeError):
        return None


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "demand-forecast" / "dfm_features.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/demand-forecast/dfm_features.md not found).")


# --- weather -------------------------------------------------------------------
def load_weather(inter_dir: Path, station_master: dict):
    """Build {station_name: {date: (high_temp, avg_temp, rain)}} for stations that
    also have coordinates in the station master (best-effort name match)."""
    obs: dict[str, dict] = {}
    for path in sorted(glob.glob(str(inter_dir / "weather_history_*.tsv"))):
        for r in read_tsv(Path(path)):
            name = r.get(STATION_COL, "").strip()
            if name not in station_master:
                continue
            d = parse_date(r.get(DATE_COL, ""))
            if d is None:
                continue
            obs.setdefault(name, {})[d] = (
                parse_float(r.get(HIGH_TEMP_COL)),
                parse_float(r.get(AVG_TEMP_COL)),
                parse_float(r.get(RAIN_COL)),
            )
    return obs


def proxy_date(target: dt.date) -> dt.date:
    """Previous-year date with the same month/day; the day before for Feb 29."""
    y = target.year - 1
    try:
        return dt.date(y, target.month, target.day)
    except ValueError:                                  # Feb 29 in a non-leap year
        return dt.date(y, target.month, target.day - 1)


def weather_value(series: dict, pdate: dt.date, idx: int):
    """Value at column idx for the proxy date, filling from up to 2 prior days."""
    for back in range(WEATHER_FILL_DAYS + 1):
        rec = series.get(pdate - dt.timedelta(days=back))
        if rec is not None and rec[idx] is not None:
            return rec[idx]
    return None


# --- reference-date calendar ---------------------------------------------------
def thursday_of_week(d: dt.date) -> dt.date:
    """The Thursday of the ISO week containing d (Mon=0 .. Thu=3 .. Sun=6)."""
    return d + dt.timedelta(days=3 - d.weekday())


def week_dates(ref: dt.date, k: int):
    """The 7 dates (Mon..Sun) of week-k relative to reference Thursday ref."""
    monday = ref - dt.timedelta(days=3 + 7 * k)
    return [monday + dt.timedelta(days=i) for i in range(7)]


def target_dates(ref: dt.date):
    """The 7 dates of week+1 (Mon R+4 .. Sun R+10)."""
    monday = ref + dt.timedelta(days=4)
    return [monday + dt.timedelta(days=i) for i in range(7)]


# --- aggregation helpers -------------------------------------------------------
def is_weekday(d: dt.date) -> bool:
    return d.isoweekday() <= 5


def is_weekend(d: dt.date) -> bool:
    return d.isoweekday() >= 6


def vals(sales_map: dict, dates, keep=lambda d: True):
    return [sales_map[d] for d in dates if d in sales_map and keep(d)]


def mean_or_none(xs):
    return statistics.fmean(xs) if xs else None


def median_or_none(xs):
    return statistics.median(xs) if xs else None


def sub_or_none(a, b):
    return None if a is None or b is None else a - b


# Ordered feature columns (after the key columns, before the target).
REGRESSION_COLS = [
    "week-1_avg_sales", "week-1_median_sales",
    "week-1_avg_weekday_sales", "week-1_median_weekday_sales",
    "week-1_avg_weekend_sales", "week-1_median_weekend_sales",
    "week-1to4_avg_sales", "week-1to4_median_sales",
    "week-1to4_avg_weekday_sales", "week-1to4_median_weekday_sales",
    "week-1to4_avg_weekend_sales", "week-1to4_median_weekend_sales",
    "delta-week-1to4_avg_sales",
    "delta-week-1to4_weekday_avg_sales",
    "delta-week-1to4_weekend_avg_sales",
] + [f"week-1to4_weekday{n}_avg_sales" for n in range(1, 8)]

WEATHER_COLS = ["week+1_high_temperature", "week+1_avg_temperature", "week+1_rainfall"]
CALENDAR_COLS = ["month_number", "is_weekend", "weekday_number",
                 "target_offdays_cos", "target_offdays_sin"]
KEY_COLS = ["store_name", "reference_date", "target_date"]
HEADER = KEY_COLS + REGRESSION_COLS + WEATHER_COLS + CALENDAR_COLS + ["actual_sales"]


def regression_features(sales_map: dict, ref: dt.date) -> dict:
    """Past-zone sales aggregates for one (store, reference_date). Constant across
    the 7 target-date rows."""
    weeks = {k: week_dates(ref, k) for k in range(1, WEEKS_BACK + 1)}
    pooled = [d for k in range(1, WEEKS_BACK + 1) for d in weeks[k]]

    w1_all, w1_wd, w1_we = (vals(sales_map, weeks[1]),
                            vals(sales_map, weeks[1], is_weekday),
                            vals(sales_map, weeks[1], is_weekend))
    p_all, p_wd, p_we = (vals(sales_map, pooled),
                         vals(sales_map, pooled, is_weekday),
                         vals(sales_map, pooled, is_weekend))
    w4_all = vals(sales_map, weeks[4])
    w4_wd = vals(sales_map, weeks[4], is_weekday)
    w4_we = vals(sales_map, weeks[4], is_weekend)

    feats = {
        "week-1_avg_sales": mean_or_none(w1_all),
        "week-1_median_sales": median_or_none(w1_all),
        "week-1_avg_weekday_sales": mean_or_none(w1_wd),
        "week-1_median_weekday_sales": median_or_none(w1_wd),
        "week-1_avg_weekend_sales": mean_or_none(w1_we),
        "week-1_median_weekend_sales": median_or_none(w1_we),
        "week-1to4_avg_sales": mean_or_none(p_all),
        "week-1to4_median_sales": median_or_none(p_all),
        "week-1to4_avg_weekday_sales": mean_or_none(p_wd),
        "week-1to4_median_weekday_sales": median_or_none(p_wd),
        "week-1to4_avg_weekend_sales": mean_or_none(p_we),
        "week-1to4_median_weekend_sales": median_or_none(p_we),
        "delta-week-1to4_avg_sales": sub_or_none(mean_or_none(w1_all), mean_or_none(w4_all)),
        "delta-week-1to4_weekday_avg_sales": sub_or_none(mean_or_none(w1_wd), mean_or_none(w4_wd)),
        "delta-week-1to4_weekend_avg_sales": sub_or_none(mean_or_none(w1_we), mean_or_none(w4_we)),
    }
    for n in range(1, 8):
        feats[f"week-1to4_weekday{n}_avg_sales"] = mean_or_none(
            vals(sales_map, pooled, lambda d, n=n: d.isoweekday() == n))
    return feats


def calendar_features(target: dt.date) -> dict:
    d = (target - dt.date(target.year, 1, 1)).days
    angle = 2 * math.pi * d / 366
    return {
        "month_number": target.month,
        "is_weekend": 1 if is_weekend(target) else 0,
        "weekday_number": target.isoweekday(),
        "target_offdays_cos": math.cos(angle),
        "target_offdays_sin": math.sin(angle),
    }


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


# --- split definitions ---------------------------------------------------------
def build_splits(sales_start: dt.date, today: dt.date):
    """Return (training_refs, test_refs, predict_refs) lists of Thursday dates."""
    # First valid reference date: first Thursday R with week-4 Monday (R-31) >= start.
    earliest = sales_start + dt.timedelta(days=3 + 7 * WEEKS_BACK)   # R-31 >= start
    first_valid = thursday_of_week(earliest)
    if first_valid < earliest:
        first_valid += dt.timedelta(days=7)

    # Last valid reference date: last Thursday on or before today (JST).
    last_valid = thursday_of_week(today)
    if last_valid > today:
        last_valid -= dt.timedelta(days=7)

    # Test: last ref = Thursday two weeks before today; 8 refs ending there.
    test_last = thursday_of_week(today - dt.timedelta(days=14))
    test_first = test_last - dt.timedelta(days=7 * 7)
    test_refs = [test_first + dt.timedelta(days=7 * i) for i in range(8)]

    # Training: first_valid .. immediately before the first test reference date.
    train_refs = []
    r = first_valid
    while r < test_first:
        train_refs.append(r)
        r += dt.timedelta(days=7)

    # Prediction: relative to today (JST). On or after Thursday, this week's
    # Thursday is already a valid reference date, so include the last two valid
    # reference dates (this week's and the previous week's); before Thursday,
    # this week's Thursday has not yet occurred, so include only the single last
    # valid reference date (the previous week's Thursday).
    if today.weekday() >= 3:  # Mon=0 .. Thu=3 .. Sun=6
        predict_refs = [last_valid - dt.timedelta(days=7), last_valid]
    else:
        predict_refs = [last_valid]
    return train_refs, test_refs, predict_refs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--today", default=None,
                    help="override the JST 'current system date' (YYYY-MM-DD), for testing")
    ap.add_argument("--limit-stores", type=int, default=None,
                    help="process only the first N stores (testing)")
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    primary = repo / "DATA" / "s03_primary"
    inter = repo / "DATA" / "s02_intermediate"
    out_dir = repo / "DATA" / "s04_feature"
    out_dir.mkdir(parents=True, exist_ok=True)

    today = parse_date(args.today) if args.today else dt.datetime.now(JST).date()

    # --- sales: store_name -> {date: sales}; track the earliest date -----------
    sales: dict[str, dict] = {}
    sales_start = None
    for r in read_tsv(primary / "sales.tsv"):
        d = parse_date(r["date"])
        if d is None:
            continue
        sales.setdefault(r["store_name"], {})[d] = float(r["sales"])
        if sales_start is None or d < sales_start:
            sales_start = d
    if sales_start is None:
        raise SystemExit("No sales rows found in DATA/s03_primary/sales.tsv")

    # --- stores (coords for the weather join) ----------------------------------
    stores = read_tsv(primary / "store.tsv")
    stores.sort(key=lambda s: s["store_name"])
    if args.limit_stores:
        stores = stores[: args.limit_stores]

    # --- weather: nearest active, temperature-capable station per store ---------
    station_master = {}
    for s in read_tsv(inter / "weather-station.tsv"):
        if s.get("End Date", "").strip() != OPEN_ENDED:
            continue                                    # active stations only
        if s.get("Temperature", "").strip() != "1":
            continue                                    # must have a temperature sensor
        lat, lon = parse_float(s["Latitude_Precipitation"]), parse_float(s["Longitude_Precipitation"])
        if lat is not None and lon is not None:
            station_master[s["Station Name (Kanji)"].strip()] = (lat, lon)
    weather = load_weather(inter, station_master)
    usable = [(n, *station_master[n]) for n in weather]

    train_refs, test_refs, predict_refs = build_splits(sales_start, today)
    print(f"Stores: {len(stores)} | sales start: {sales_start} | today (JST): {today}")
    print(f"Reference Thursdays -> training: {len(train_refs)} "
          f"({train_refs[0]}..{train_refs[-1]}) | "
          f"test: {len(test_refs)} ({test_refs[0]}..{test_refs[-1]}) | "
          f"prediction: {len(predict_refs)} ({predict_refs[0]})")
    print(f"Weather stations usable: {len(usable)}")

    # Per-store nearest weather series, computed once.
    store_series = {}
    for store in stores:
        slat, slon = float(store["latitude"]), float(store["longitude"])
        nearest = min(usable, key=lambda s: haversine_m(slat, slon, s[1], s[2]), default=None)
        store_series[store["store_name"]] = weather.get(nearest[0], {}) if nearest else {}

    splits = {
        "training_dataset.tsv": (train_refs, True),
        "test_dataset.tsv": (test_refs, True),
        "predict_dataset.tsv": (predict_refs, False),
    }

    for filename, (refs, has_target) in splits.items():
        out_path = out_dir / filename
        rows = 0
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(HEADER)
            for store in stores:
                name = store["store_name"]
                sales_map = sales.get(name, {})
                series = store_series[name]
                for ref in refs:
                    reg = regression_features(sales_map, ref)
                    for target in target_dates(ref):
                        cal = calendar_features(target)
                        wx = {
                            "week+1_high_temperature": weather_value(series, proxy_date(target), 0),
                            "week+1_avg_temperature": weather_value(series, proxy_date(target), 1),
                            # The one-year-lag proxy carries no real signal for rainfall, so
                            # week+1_rainfall always falls back to the no-rain default (0).
                            "week+1_rainfall": 0.0,
                        }
                        actual = sales_map.get(target) if has_target else None
                        row = [name, ref.isoformat(), target.isoformat()]
                        row += [fmt(reg[c]) for c in REGRESSION_COLS]
                        row += [fmt(wx[c]) for c in WEATHER_COLS]
                        row += [fmt(cal[c]) for c in CALENDAR_COLS]
                        row.append(fmt(actual))
                        w.writerow(row)
                        rows += 1
        print(f"  [OK] {out_path.relative_to(repo)} ({rows:,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
