"""Associate web-found events with the stores they are near.

A **deterministic** spatial step (not an LLM task), the analogue of the synthetic
event influence in `docs/pipeline/synthetics/Sales.md`: read every store from
`DATA/s03_primary/store.tsv`, and for each geocoded event (from `geocode-locations`) keep the
(store, event) pairs whose great-circle distance is within 200 m — the same
`EVENT_RADIUS_M` the sales model uses.

The map-distance algorithm is the haversine defined in `docs/pipeline/synthetics/Sales.md`
§"Map distance algorithm"; this mirrors `pipeline/skills/synthesize-sales` (same
`EARTH_RADIUS_M` and `2*atan2` form) so agent-side matching agrees with the pipeline.
Stdlib only.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
from pathlib import Path

PROMPT_VERSION = "map_match_events/v1"  # named for parity with the tasks; no prompt here

# Per docs/pipeline/synthetics/Sales.md: mean Earth radius, and the 200 m event influence radius.
EARTH_RADIUS_M = 6_371_000.0
EVENT_RADIUS_M = 200.0

JST = dt.timezone(dt.timedelta(hours=9))  # event dates/"tomorrow" are reckoned in JST


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (docs/pipeline/synthetics/Sales.md §Map distance)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def jst_tomorrow() -> dt.date:
    """The next date relative to the current system time, in JST."""
    return dt.datetime.now(JST).date() + dt.timedelta(days=1)


def _parse_date(value) -> dt.date | None:
    """Parse a 'YYYY-MM-DD' string to a date; None if blank or unparseable."""
    try:
        return dt.date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_active(event: dict, tomorrow: dt.date) -> int:
    """1 if `tomorrow` falls within the event's date range, else 0.

    - complete period [start_date, end_date]: 1 iff start <= tomorrow <= end
    - half-open (only one date is known): 1 iff tomorrow is on the known bound's inside
      ([start, +inf) if only start, (-inf, end] if only end)
    - unknown period (no valid dates): 1
    """
    start = _parse_date(event.get("start_date"))
    end = _parse_date(event.get("end_date"))
    if start and end:
        return 1 if start <= tomorrow <= end else 0
    if start:                 # only start known -> [start, +inf)
        return 1 if tomorrow >= start else 0
    if end:                   # only end known -> (-inf, end]
        return 1 if tomorrow <= end else 0
    return 1                  # period unknown


def load_stores(path: str | Path) -> list[dict]:
    """Read store.tsv -> [{prefecture, store_name, latitude, longitude}]. Skips bad coords."""
    stores = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            stores.append({
                "prefecture": row.get("prefecture", ""),
                "store_name": row.get("store_name", ""),
                "latitude": lat,
                "longitude": lon,
            })
    return stores


def _coords(event: dict) -> tuple[float, float] | None:
    """Event (lat, lon) as floats, or None if it wasn't geocoded / is blank."""
    try:
        return float(event["latitude"]), float(event["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def match_events_to_stores(events: list[dict], stores: list[dict],
                           radius_m: float = EVENT_RADIUS_M,
                           tomorrow: dt.date | None = None) -> list[dict]:
    """Return (store, event) pairs within `radius_m`, sorted by store then distance.

    Events without usable coordinates (geocode miss) are skipped. Each match carries the
    store identity, the event's descriptive fields, the rounded `distance_m`, and an
    `active` flag (1 if the event runs on `tomorrow` — JST next date by default; see
    `is_active`).
    """
    tomorrow = tomorrow or jst_tomorrow()
    matches = []
    for ev in events:
        coords = _coords(ev)
        if coords is None:
            continue
        elat, elon = coords
        active = is_active(ev, tomorrow)
        for st in stores:
            dist = haversine_m(st["latitude"], st["longitude"], elat, elon)
            if dist <= radius_m:
                matches.append({
                    "prefecture": st["prefecture"],
                    "store_name": st["store_name"],
                    "event_name": ev.get("event_name", ""),
                    "event_type": ev.get("event_type", ""),
                    "start_date": ev.get("start_date", ""),
                    "end_date": ev.get("end_date", ""),
                    "active": active,
                    "venue": ev.get("venue", ""),
                    "location": ev.get("location", ""),
                    "latitude": elat,
                    "longitude": elon,
                    "distance_m": round(dist, 1),
                    "source_url": ev.get("source_url", ""),
                })
    matches.sort(key=lambda m: (m["store_name"], m["distance_m"]))
    return matches
