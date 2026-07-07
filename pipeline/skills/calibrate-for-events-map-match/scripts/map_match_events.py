#!/usr/bin/env python3
"""Calibrate for events — step 3/4: map-match geocoded events to stores (deterministic).

Implements docs/pipeline/calibration/Calibrate-for-events-map-match.md.

  DATA/s09_calibration/geocoded_events.json  +  DATA/s03_primary/store.tsv
    -> haversine match (docs/pipeline/synthetics/Sales.md) within --radius-m
    -> DATA/s09_calibration/mapped_events.json

Keeps every (store, event) pair whose great-circle distance is within `--radius-m`
(default 500 m — deliberately wider than the 200 m event-influence radius in Sales.md,
because web-found events are geocoded to venues only approximately). Each pair carries an
`active` flag (1 if the event runs tomorrow, JST). Imports the agent-service task module
`agent.tasks.map_match_events` (stdlib-only, torch-free) so matching agrees with the API.

Deterministic (no LLM, no network, no cost). Third of four independent event-calibration
skills:

  extract -> geo-code -> map-match (this) -> estimate-attendance
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# --------------------------------------------------------------------- repo helpers
def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "pipeline" / "config" / "config.yaml").exists():
            return p
    raise SystemExit("Could not locate repo root "
                     "(pipeline/config/config.yaml not found).")


def read_json_list(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} is not a JSON list.")
    return data


def write_json_list(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ------------------------------------------------------------------------- map-match
def map_match(repo, *, radius_m, in_path, out_path, mm):
    if not in_path.exists():
        raise SystemExit(f"Missing input: {in_path} — run the `geo-code` skill first.")
    events = read_json_list(in_path)

    store_path = repo / "DATA" / "s03_primary" / "store.tsv"
    if not store_path.exists():
        raise SystemExit(f"Missing store file: {store_path} — run synthesize-stores first.")
    stores = mm.load_stores(store_path)
    matches = mm.match_events_to_stores(events, stores, radius_m=radius_m)

    write_json_list(out_path, matches)
    print(f"Wrote {out_path} ({len(matches)} store-event pairs within {radius_m:.0f}m; "
          f"{len(events)} events, {len(stores)} stores).")
    return matches


# ---------------------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--radius-m", type=float, default=500.0, help="map-match radius in metres")
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    agent_service = repo / "agent-service"

    out_dir = repo / "DATA" / "s09_calibration"
    in_path = out_dir / "geocoded_events.json"
    out_path = out_dir / "mapped_events.json"

    # Import the agent-service map-match task (stdlib-only, torch-free) so matching agrees
    # with the API and with synthesize-sales' haversine.
    if str(agent_service) not in sys.path:
        sys.path.insert(0, str(agent_service))
    try:
        from agent.tasks import map_match_events as mm
    except ImportError as exc:
        raise SystemExit(f"Could not import agent-service task module from "
                         f"{agent_service}: {exc}")

    map_match(repo, radius_m=args.radius_m, in_path=in_path, out_path=out_path, mm=mm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
