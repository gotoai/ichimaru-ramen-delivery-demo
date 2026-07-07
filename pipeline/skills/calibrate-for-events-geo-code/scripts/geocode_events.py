#!/usr/bin/env python3
"""Calibrate for events — step 2/4: geocode extracted events (deterministic; paid).

Implements docs/pipeline/calibration/Calibrate-for-events-geo-code.md.

  DATA/s09_calibration/extracted_events.json
    -> Google Geocoding (venue + location, components=country:JP)
    -> DATA/s09_calibration/geocoded_events.json

Resolves each extracted event to coordinates by importing the agent-service task module
`agent.tasks.geocode_locations` (stdlib-only, torch-free) so this skill geocodes exactly
as the API would. Adds `latitude`, `longitude`, `formatted_address`, `location_type`,
`geocode_confidence`, `place_id`, and `geocode_status` to each event. Failures are
per-event and non-fatal (empty coords + a status note). A persistent query cache at
`agent-service/.cache/geocode_cache.json` means repeated venues aren't billed twice.

Live / paid (bills Google Geocoding per cache-missed query); not reproducible. Reads
GOOGLE_GEOCODING_API_KEY from the environment or agent-service/.env; never logs it. Second
of four independent event-calibration skills:

  extract -> geo-code (this) -> map-match -> estimate-attendance
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------- repo / env helpers
def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "pipeline" / "config" / "config.yaml").exists():
            return p
    raise SystemExit("Could not locate repo root "
                     "(pipeline/config/config.yaml not found).")


def read_env_value(env_path: Path, key: str, default: str = "") -> str:
    """`key` from the environment, else parsed from `env_path` (.env); never logged."""
    v = os.environ.get(key, "").strip()
    if v:
        return v
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == key:
                return val.strip().strip('"').strip("'")
    return default


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


# ------------------------------------------------------------------------- geocode
def geocode_events(repo, *, geocode_key, in_path, out_path, geo):
    if not in_path.exists():
        raise SystemExit(f"Missing input: {in_path} — run the `extract` skill first.")
    if not geocode_key:
        raise SystemExit("GOOGLE_GEOCODING_API_KEY not found (env or agent-service/.env).")
    events = read_json_list(in_path)

    cache_path = repo / "agent-service" / ".cache" / "geocode_cache.json"
    cache = geo.load_cache(cache_path)
    before = len(cache)
    enriched = geo.enrich_events(events, api_key=geocode_key, cache=cache)
    if len(cache) != before:
        geo.save_cache(cache_path, cache)
    n_ok = sum(1 for e in enriched if e.get("geocode_status") == "ok")

    write_json_list(out_path, enriched)
    print(f"Wrote {out_path} ({n_ok}/{len(enriched)} events geocoded; "
          f"cache now {len(cache)} queries).")
    return enriched


# ---------------------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    agent_service = repo / "agent-service"
    env_path = agent_service / ".env"
    geocode_key = read_env_value(env_path, "GOOGLE_GEOCODING_API_KEY")

    out_dir = repo / "DATA" / "s09_calibration"
    in_path = out_dir / "extracted_events.json"
    out_path = out_dir / "geocoded_events.json"

    # Import the agent-service geocoding task (stdlib-only, torch-free) so this skill
    # geocodes identically to the API.
    if str(agent_service) not in sys.path:
        sys.path.insert(0, str(agent_service))
    try:
        from agent.tasks import geocode_locations as geo
    except ImportError as exc:
        raise SystemExit(f"Could not import agent-service task module from "
                         f"{agent_service}: {exc}")

    geocode_events(repo, geocode_key=geocode_key, in_path=in_path, out_path=out_path, geo=geo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
