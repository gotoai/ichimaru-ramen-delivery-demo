#!/usr/bin/env python3
"""Merge the weather and event calibrations into the final week+1 calibrated sales.

Implements docs/pipeline/calibration/Calibrate-merge.md. The weather calibration is a
multiplicative correction (bias + temperature/rainfall); the event calibration is an
additive extra demand in bowls. They act on different scales, so:

    calibrated_sales = weather_calibrated_sales + event_added_demand

The weather-calibrated rows are the spine (one per prediction); event demand is summed
onto the stores and target dates where events apply (0 everywhere else).

Inputs (DATA/s09_calibration/):
  - weather_calibrated_sales.tsv    (calibrate-for-weather) — row spine + weather number
  - weather_calibration_info.json   (calibrate-for-weather) — weather factor breakdown
  - estimated_events.json           (calibrate-for-events-estimate-attendance) — per (store, event) demand

Writes DATA/s09_calibration/calibrated_sales.tsv (one row per prediction) and
calibration_info.json (per-prediction breakdown of both calibrations).

Stdlib only; deterministic (pure file merge — no API, no network).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path

SELF_CHECK_TOL = 1e-6
OUT_DECIMALS = 6

OUT_COLS = [
    "prefecture", "store_name", "reference_date", "target_date",
    "predicted_sales", "weather_calibrated_sales",
    "event_added_demand", "event_count", "calibrated_sales",
]


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline" / "calibration" / "Calibrate-merge.md").exists():
            return p
    raise SystemExit("Could not locate repo root "
                     "(docs/pipeline/calibration/Calibrate-merge.md not found).")


def read_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_json_list(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} is not a JSON list.")
    return data


def to_float(value):
    """Parse a numeric cell; blank / non-numeric -> None."""
    if value is None:
        return None
    v = str(value).strip()
    if v == "" or v.lower() == "nan":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def to_int(value, default: int = 0) -> int:
    f = to_float(value)
    return int(round(f)) if f is not None else default


def parse_date(value):
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_active_on(start, end, target: date) -> bool:
    """Whether an event covers `target` (same rule as map_match_events.is_active).

    both dates known -> start <= target <= end; only start -> target >= start;
    only end -> target <= end; neither (period unknown) -> always active.
    """
    s, e = parse_date(start), parse_date(end)
    if s and e:
        return s <= target <= e
    if s:
        return target >= s
    if e:
        return target <= e
    return True


def fmt(value) -> str:
    """Format a float for the TSV; blank for None."""
    return "" if value is None else str(round(value, OUT_DECIMALS))


def load_store_events(path: Path):
    """estimated_events.json -> {store_name: [event dict, ...]} de-duplicated by event_name.

    Keeps the first (nearest — the file is sorted by store then distance) occurrence of
    each event_name per store, so an event surfaced from several search locations counts
    once. Drops pairs with estimated_demand <= 0 (no-estimate / rounded-to-zero).
    """
    if not path.exists():
        print(f"WARNING: {path} not found — no event demand will be added "
              "(run the calibrate-for-events-* chain first).", file=sys.stderr)
        return {}
    store_events: "OrderedDict[str, OrderedDict[str, dict]]" = OrderedDict()
    for row in read_json_list(path):
        store = row.get("store_name", "")
        name = row.get("event_name", "")
        demand = to_int(row.get("estimated_demand"))
        if not store or not name or demand <= 0:
            continue
        by_name = store_events.setdefault(store, OrderedDict())
        by_name.setdefault(name, {          # first (nearest) wins
            "event_name": name,
            "event_type": row.get("event_type", ""),
            "start_date": row.get("start_date", ""),
            "end_date": row.get("end_date", ""),
            "distance_m": row.get("distance_m", ""),
            "estimated_demand": demand,
            "source_url": row.get("source_url", ""),
        })
    return {store: list(by_name.values()) for store, by_name in store_events.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    cal = repo / "DATA" / "s09_calibration"
    weather_tsv = cal / "weather_calibrated_sales.tsv"
    weather_info = cal / "weather_calibration_info.json"
    events_json = cal / "estimated_events.json"
    for p in (weather_tsv, weather_info):
        if not p.exists():
            raise SystemExit(f"Missing input: {p} — run calibrate-for-weather first.")

    # Weather factor breakdown, keyed by (store, reference_date, target_date).
    info_by_key = {
        (o["store_name"], o["reference_date"], o["target_date"]): o
        for o in read_json_list(weather_info)
    }
    store_events = load_store_events(events_json)

    out_rows: list[list] = []
    info: list[dict] = []
    n_with_events = 0
    total_demand = 0

    for r in read_tsv(weather_tsv):
        pref, store = r["prefecture"], r["store_name"]
        ref, tgt = r["reference_date"], r["target_date"]
        predicted = to_float(r["predicted_sales"])
        weather_cal = to_float(r["calibrated_sales"])
        tgt_date = parse_date(tgt)

        # Events near this store that are active on this target date.
        contributing = []
        if tgt_date is not None:
            for ev in store_events.get(store, []):
                if is_active_on(ev["start_date"], ev["end_date"], tgt_date):
                    contributing.append(ev)
        event_demand = sum(ev["estimated_demand"] for ev in contributing)
        calibrated = (weather_cal + event_demand) if weather_cal is not None else None

        if event_demand:
            n_with_events += 1
            total_demand += event_demand

        out_rows.append([
            pref, store, ref, tgt,
            fmt(predicted), fmt(weather_cal),
            event_demand, len(contributing), fmt(calibrated),
        ])

        winfo = info_by_key.get((store, ref, tgt), {})
        check = (calibrated is not None
                 and abs((weather_cal + event_demand) - calibrated) <= SELF_CHECK_TOL)
        info.append({
            "prefecture": pref, "store_name": store,
            "reference_date": ref, "target_date": tgt,
            "predicted_sales": predicted,
            "weather_calibrated_sales": weather_cal,
            "event_added_demand": event_demand,
            "calibrated_sales": calibrated,
            "formula": "calibrated = weather_calibrated + event_added_demand",
            "weather": {
                "weather_applied": winfo.get("weather_applied",
                                             r.get("weather_applied") == "true"),
                "inputs": winfo.get("inputs"),
                "factors": winfo.get("factors", []),
            },
            "events": [{
                "event_name": ev["event_name"], "event_type": ev["event_type"],
                "start_date": ev["start_date"], "end_date": ev["end_date"],
                "distance_m": ev["distance_m"], "estimated_demand": ev["estimated_demand"],
                "source_url": ev["source_url"],
            } for ev in contributing],
            "self_check_ok": check,
        })

    cal.mkdir(parents=True, exist_ok=True)
    tsv_path = cal / "calibrated_sales.tsv"
    json_path = cal / "calibration_info.json"
    with open(tsv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(OUT_COLS)
        w.writerows(out_rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {tsv_path} and {json_path} ({len(out_rows)} predictions).")
    print(f"  with an event uplift: {n_with_events}  (total added demand: {total_demand} bowls)")
    print(f"  weather-only:         {len(out_rows) - n_with_events}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
