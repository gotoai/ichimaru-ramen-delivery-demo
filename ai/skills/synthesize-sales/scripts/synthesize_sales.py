#!/usr/bin/env python3
"""Synthesize daily store sales (bowls of ramen) for the Ichimaru demo.

Implements the algorithm in docs/synthetics/Sales.md: for every store in
DATA/s03_primary/store.tsv and every date in the sales_history window, build a
sales value from a weekday/weekend baseline and additive/multiplicative
influences from nearby home buildings, competitors, events, temperature and
rain, then round and clamp (>= 10). Output -> DATA/s03_primary/sales.tsv.

Reads sampling/seed/time-horizon settings from config/config.yaml (PyYAML). All
other work uses the Python standard library; randomness is reproducible from a
single RNG seeded with synthetics/random_seed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import math
import random
import sys
from pathlib import Path

import yaml

EARTH_RADIUS_M = 6_371_000.0
JST = dt.timezone(dt.timedelta(hours=9))
OPEN_ENDED = "9999-99-99"

# Influence radii (metres) and the weather value columns, per Sales.md.
HOME_RADIUS_M = 500.0
COMP_RADIUS_M = 50.0
EVENT_RADIUS_M = 200.0
TEMP_COL = "最高気温(℃)"
RAIN_COL = "降水量の合計(mm)"
STATION_COL = "観測地点"
DATE_COL = "日付"
FILL_FORWARD_DAYS = 3
MIN_SALES = 10


# --- geometry ------------------------------------------------------------------
def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# --- sampling ------------------------------------------------------------------
def sample(rng: random.Random, spec: dict) -> float:
    """Draw one value from a config sampling spec (beta scale / gaussian noise)."""
    kernel = spec["kernel"]
    p = spec["parameters"]
    if kernel == "beta":
        x = rng.betavariate(p["alpha"], p["beta"])
        if spec.get("normalize"):
            x /= p["alpha"] / (p["alpha"] + p["beta"])   # expectation -> 1.0
        return x
    if kernel == "gaussian":
        x = rng.gauss(p["mu"], p["sigma"])
        bounds = p.get("bounds")
        if bounds:
            x = min(bounds[1], max(bounds[0], x))         # clip to bounds
        return x
    raise ValueError(f"Unknown sampling kernel: {kernel}")


# --- data loading --------------------------------------------------------------
def read_tsv(path: Path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def parse_date(s: str):
    s = s.strip()
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


def active_window(open_s: str, end_s: str):
    """Return (open_date, end_date|date.max) for an entity; None if unparsable."""
    o = parse_date(open_s)
    if o is None:
        return None
    e = dt.date.max if end_s.strip() == OPEN_ENDED else parse_date(end_s)
    return o, (e or dt.date.max)


def daterange(start: dt.date, end: dt.date):
    d = start
    one = dt.timedelta(days=1)
    while d <= end:
        yield d
        d += one


def default_period():
    end = dt.datetime.now(JST).date() - dt.timedelta(days=2)
    return dt.date(end.year - 2, 1, 1), end


# --- weather -------------------------------------------------------------------
def load_weather(inter_dir: Path, station_master: dict):
    """Build {station_name: {date: (temp, rain)}} for stations that also have
    coordinates in the station master (best-effort name match; others dropped)."""
    obs: dict[str, dict] = {}
    # Matches both the combined `weather_history_all_*.tsv` and the per-prefecture
    # fallback `weather_history_<prefecture>_*.tsv` files.
    for path in sorted(glob.glob(str(inter_dir / "weather_history_*.tsv"))):
        for r in read_tsv(Path(path)):
            name = r.get(STATION_COL, "").strip()
            if name not in station_master:
                continue                                   # unmatched -> no coords
            d = parse_date(r.get(DATE_COL, ""))
            if d is None:
                continue
            obs.setdefault(name, {})[d] = (
                parse_float(r.get(TEMP_COL)), parse_float(r.get(RAIN_COL)))
    return obs


def fill_forward(series: dict, date: dt.date, idx: int):
    """Value for `date` (idx 0=temp, 1=rain), filling forward up to 3 prior days."""
    for back in range(FILL_FORWARD_DAYS + 1):
        rec = series.get(date - dt.timedelta(days=back))
        if rec is not None and rec[idx] is not None:
            return rec[idx]
    return None


# --- main ----------------------------------------------------------------------
def get_cfg(d, *keys):
    """Fetch a nested config value, raising a clear error if a key is missing."""
    for k in keys:
        try:
            d = d[k]
        except (KeyError, TypeError):
            raise SystemExit(f"config.yaml: missing key '{'/'.join(map(str, keys))}'")
    return d


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/profiles/Locations.md not found).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (override sales_history start)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (override sales_history end)")
    ap.add_argument("--limit-stores", type=int, default=None, help="process only the first N stores (testing)")
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    primary = repo / "DATA" / "s03_primary"
    inter = repo / "DATA" / "s02_intermediate"
    config = yaml.safe_load((repo / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    rng = random.Random(int(get_cfg(config, "synthetics", "random_seed")))
    # Pre-extract and validate the (scale, noise) sampling spec for each step.
    spec = {s: (get_cfg(config, "synthetics", "sales", s, "scale_sampling"),
                get_cfg(config, "synthetics", "sales", s, "noise_sampling"))
            for s in ("baseline", "home_building", "competitor", "event",
                      "weather_temperature", "weather_rain")}
    base_s, base_n = spec["baseline"]
    home_s, home_n = spec["home_building"]
    comp_s, comp_n = spec["competitor"]
    evt_s, evt_n = spec["event"]
    temp_s, temp_n = spec["weather_temperature"]
    rain_s, rain_n = spec["weather_rain"]

    start = parse_date(args.start) if args.start else None
    end = parse_date(args.end) if args.end else None
    if start is None or end is None:
        d_start, d_end = default_period()
        start = start or d_start
        end = end or d_end
    dates = list(daterange(start, end))

    # --- load entities ---------------------------------------------------------
    stores = read_tsv(primary / "store.tsv")
    stores.sort(key=lambda s: s["store_name"])
    if args.limit_stores:
        stores = stores[: args.limit_stores]

    homes = []
    for h in read_tsv(primary / "home_building.tsv"):
        win = active_window(h["open_date"], h["end_date"])
        homes.append((float(h["latitude"]), float(h["longitude"]), int(h["unit"]), win))
    comps = []
    for c in read_tsv(primary / "competitor.tsv"):
        win = active_window(c["open_date"], c["end_date"])
        comps.append((float(c["latitude"]), float(c["longitude"]),
                      float(c["weekday_sale_baseline"]), float(c["weekend_sale_baseline"]), win))
    events = []
    for e in read_tsv(primary / "event.tsv"):
        ed = parse_date(e["event_date"])
        events.append((float(e["latitude"]), float(e["longitude"]), int(e["people"]), ed))

    # station master: name -> (lat, lon)
    station_master = {}
    for s in read_tsv(inter / "weather-station.tsv"):
        lat, lon = parse_float(s["Latitude_Precipitation"]), parse_float(s["Longitude_Precipitation"])
        if lat is not None and lon is not None:
            station_master[s["Station Name (Kanji)"].strip()] = (lat, lon)
    weather = load_weather(inter, station_master)
    usable_stations = [(n, *station_master[n]) for n in weather]   # both coords + obs
    print(f"Stores: {len(stores)} | dates: {len(dates)} "
          f"({start} .. {end}) | weather stations usable: {len(usable_stations)}")

    # --- synthesize ------------------------------------------------------------
    out_path = primary / "sales.tsv"
    tty = sys.stdout.isatty()
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["prefecture", "store_name", "date", "sales"])

        for si, store in enumerate(stores):
            slat, slon = float(store["latitude"]), float(store["longitude"])
            wd_base, we_base = float(store["weekday_sale_baseline"]), float(store["weekend_sale_baseline"])

            # pre-filter nearby entities once (distance is date-independent)
            near_homes = [(unit, win, d) for (la, lo, unit, win) in homes
                          if (d := haversine_m(slat, slon, la, lo)) <= HOME_RADIUS_M]
            near_comps = [(wd, we, win, d) for (la, lo, wd, we, win) in comps
                          if (d := haversine_m(slat, slon, la, lo)) <= COMP_RADIUS_M]
            ev_by_date: dict = {}
            for (la, lo, people, ed) in events:
                if ed is not None and (d := haversine_m(slat, slon, la, lo)) <= EVENT_RADIUS_M:
                    ev_by_date.setdefault(ed, []).append((people, d))
            nearest = min(usable_stations, key=lambda s: haversine_m(slat, slon, s[1], s[2]),
                          default=None)
            series = weather.get(nearest[0], {}) if nearest else {}

            for date in dates:
                weekend = date.weekday() >= 5
                y = (we_base if weekend else wd_base) * sample(rng, base_s) * sample(rng, base_n)

                for unit, win, dh in near_homes:
                    if win and win[0] <= date <= win[1]:
                        y += unit / 10 * sample(rng, home_s) \
                            * (100 / (100 + dh)) * sample(rng, home_n)

                for wd, we, win, dc in near_comps:
                    if win and win[0] <= date <= win[1]:
                        bc = we if weekend else wd
                        y += -bc / 4 * sample(rng, comp_s) * (10 / (10 + dc)) * sample(rng, comp_n)

                for people, de in ev_by_date.get(date, []):
                    y += people / 20 * sample(rng, evt_s) * (50 / (50 + de)) * sample(rng, evt_n)

                th = fill_forward(series, date, 0)
                if th is not None:
                    y *= (1 + (20 - th) * 0.02 * sample(rng, temp_s)) * sample(rng, temp_n)

                vr = fill_forward(series, date, 1)
                if vr is not None:
                    y *= (30 / (30 + vr) * sample(rng, rain_s)) * sample(rng, rain_n)

                w.writerow([store["prefecture"], store["store_name"], date.isoformat(),
                            max(MIN_SALES, round(y))])

            if tty:
                sys.stdout.write(f"\r\033[K  synthesizing {si + 1}/{len(stores)} stores")
                sys.stdout.flush()
        if tty:
            sys.stdout.write("\n")

    print(f"  [OK] {out_path.relative_to(repo)} "
          f"({len(stores) * len(dates):,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
