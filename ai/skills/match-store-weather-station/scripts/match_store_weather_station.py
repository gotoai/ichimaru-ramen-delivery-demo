#!/usr/bin/env python3
"""Match each Ichimaru store to its nearest usable weather station.

Implements docs/synthetics/Match-store-weather-station.md. Reproduces — and
persists — the exact store->station assignment the Demand Forecast Model uses for
its weather features (see dfm-create-features / create_features.py): the nearest
**active** weather station (master End Date = 9999-99-99) that also has rows in the
downloaded weather history, by great-circle (haversine) distance.

  1. station_master: active, temperature-capable stations from
     DATA/s02_intermediate/weather-station.tsv that have precipitation coordinates
     -> {name: (lat, lon, number)}. Stations without a temperature sensor
     (Temperature flag != 1) are excluded up front, so no store is ever matched to a
     station that cannot report temperature.
  2. usable: the subset of those stations that actually appear in
     DATA/s02_intermediate/weather_history_*.tsv (first-seen order preserved), so a
     store is never matched to a station with no observations.
  3. For each store in DATA/s03_primary/store.tsv (sorted by store_name), pick the
     nearest usable station by haversine_m.
  4. Write DATA/s03_primary/matched_store_weather_station.tsv (UTF-8 TSV, header):
     prefecture, store_name, store_latitude, store_longitude, station_number,
     station_name, station_latitude, station_longitude, distance_m.

Standard library only; deterministic for fixed inputs.
"""
from __future__ import annotations

import argparse
import csv
import glob
import math
import sys
from pathlib import Path

EARTH_RADIUS_M = 6_371_000.0
OPEN_ENDED = "9999-99-99"
TEMP_CAPABLE = "1"               # weather-station.tsv "Temperature" flag for a temperature sensor
STATION_COL = "観測地点"          # station name in the weather-history files

OUT_COLS = [
    "prefecture", "store_name", "store_latitude", "store_longitude",
    "station_number", "station_name", "station_latitude", "station_longitude",
    "distance_m",
]


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def read_tsv(path: Path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def parse_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "synthetics" / "Match-store-weather-station.md").exists():
            return p
    raise SystemExit(
        "Could not locate repo root (docs/synthetics/Match-store-weather-station.md not found).")


def require(path: Path, hint: str) -> Path:
    if not path.exists():
        raise SystemExit(f"Missing input: {path} — {hint}")
    return path


def load_station_master(station_tsv: Path) -> dict:
    """Active, temperature-capable stations with coordinates -> (lat, lon, number).

    Excludes stations that are inactive (End Date != 9999-99-99), lack precipitation
    coordinates, or have no temperature sensor (Temperature flag != 1) — the last
    rule keeps every store's matched station able to report temperature.
    """
    master = {}
    for s in read_tsv(station_tsv):
        if s.get("End Date", "").strip() != OPEN_ENDED:
            continue
        if s.get("Temperature", "").strip() != TEMP_CAPABLE:
            continue
        lat = parse_float(s.get("Latitude_Precipitation"))
        lon = parse_float(s.get("Longitude_Precipitation"))
        if lat is None or lon is None:
            continue
        master[s["Station Name (Kanji)"].strip()] = (lat, lon, s.get("Station Number", "").strip())
    return master


def usable_stations(inter_dir: Path, master: dict) -> list:
    """Stations from master that also appear in the weather history, first-seen order."""
    seen, ordered = set(), []
    for path in sorted(glob.glob(str(inter_dir / "weather_history_*.tsv"))):
        for r in read_tsv(Path(path)):
            name = r.get(STATION_COL, "").strip()
            if name in master and name not in seen:
                seen.add(name)
                ordered.append(name)
    return [(n, *master[n]) for n in ordered]     # (name, lat, lon, number)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    inter = repo / "DATA" / "s02_intermediate"
    store_tsv = repo / "DATA" / "s03_primary" / "store.tsv"
    station_tsv = inter / "weather-station.tsv"
    out_path = repo / "DATA" / "s03_primary" / "matched_store_weather_station.tsv"

    require(store_tsv, "run the synthesize-stores skill first.")
    require(station_tsv, "run the retrieve-weather-station skill first.")

    master = load_station_master(station_tsv)
    usable = usable_stations(inter, master)
    if not usable:
        raise SystemExit(
            "No usable weather stations (active + coordinates + present in weather history). "
            "Run retrieve-weather-station and retrieve-weather-history first.")

    stores = read_tsv(store_tsv)
    stores.sort(key=lambda s: s["store_name"])

    rows = []
    for store in stores:
        slat, slon = parse_float(store["latitude"]), parse_float(store["longitude"])
        if slat is None or slon is None:
            raise SystemExit(f"Store {store['store_name']} has no coordinates in store.tsv.")
        name, stlat, stlon, number = min(
            usable, key=lambda s: haversine_m(slat, slon, s[1], s[2]))
        rows.append({
            "prefecture": store.get("prefecture", ""),
            "store_name": store["store_name"],
            "store_latitude": store["latitude"],
            "store_longitude": store["longitude"],
            "station_number": number,
            "station_name": name,
            "station_latitude": stlat,
            "station_longitude": stlon,
            "distance_m": round(haversine_m(slat, slon, stlat, stlon), 1),
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS, delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    print(f"Matched {len(rows)} stores to {len(usable)} usable stations -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
