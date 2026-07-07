"""CLI to exercise the tasks during the performance/quality spike (no web API yet).

    python -m agent.cli extract-events [--input searched_events.tsv] [--location 東京都世田谷区] [--limit-items 10]
    python -m agent.cli geocode-locations --input events.json
    python -m agent.cli map-match-events --input events_geo.json [--radius-m 200]
    python -m agent.cli estimate-attendance --input events.json
    python -m agent.cli present-messages --input events.json [--format json] [--language ja] [--style brief]

`extract-events` defaults to the pipeline's DATA/s08_search/searched_events.tsv.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import config
from .tasks import (
    estimate_attendance,
    extract_events,
    geocode_locations,
    map_match_events,
    present_messages,
)


def _read_events_tsv(path: Path, location: str | None, limit: int | None):
    """Load searched_events.tsv rows for one location -> (location, [items])."""
    rows = list(csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"))
    if not rows:
        raise SystemExit(f"{path} is empty.")
    if location is None:
        location = rows[0]["location"]           # default: first location in the file
    items = [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "content": r.get("content", ""), "published_date": r.get("published_date", "")}
        for r in rows if r.get("location") == location
    ]
    if not items:
        raise SystemExit(f"No rows for location {location!r} in {path}.")
    if limit:
        items = items[:limit]
    return location, items


def _print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_extract_events(args) -> int:
    path = Path(args.input) if args.input else config.REPO_ROOT / "DATA" / "s08_search" / "searched_events.tsv"
    if not path.exists():
        raise SystemExit(f"Missing input: {path} — run the search-events skill first.")
    location, items = _read_events_tsv(path, args.location, args.limit_items)
    print(f"# extract-events: location={location} items={len(items)}", file=sys.stderr)
    if args.raw:
        from .llm import get_llm
        print(get_llm().generate(extract_events.build_messages(items, location),
                                 do_sample=False, max_new_tokens=2048))
        return 0
    events = extract_events.extract_events(items, location)
    if not events:
        print("# extract-events: parsed 0 events — re-run with --raw to inspect the model output",
              file=sys.stderr)
    _print_json(events)
    return 0


def cmd_estimate_attendance(args) -> int:
    text = Path(args.input).read_text(encoding="utf-8").strip()
    try:
        events = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"{args.input} is not valid JSON ({exc}). It should contain only the events "
            "array from `extract-events`. Re-run: python -m agent.cli extract-events ... > <file>")
    if args.raw:
        from .llm import get_llm
        print(get_llm().generate(estimate_attendance.build_messages(events),
                                 do_sample=False, max_new_tokens=2048))
        return 0
    estimates = estimate_attendance.estimate_attendance(events)
    if not estimates:
        print("# estimate-attendance: parsed 0 estimates — re-run with --raw to inspect the model output",
              file=sys.stderr)
    _print_json(estimates)
    return 0


def cmd_geocode_locations(args) -> int:
    events = json.loads(Path(args.input).read_text(encoding="utf-8"))
    api_key = args.api_key or config.GOOGLE_GEOCODING_API_KEY
    if not api_key:
        raise SystemExit(
            "No Google Geocoding key. Set GOOGLE_GEOCODING_API_KEY in agent-service/.env "
            "or pass --api-key.")

    cache_path = None if args.no_cache else (args.cache or config.GEOCODE_CACHE)
    cache = geocode_locations.load_cache(cache_path) if cache_path else {}
    before = len(cache)

    enriched = geocode_locations.enrich_events(events, api_key=api_key,
                                               language=args.language, region=args.region,
                                               cache=cache)

    if cache_path and len(cache) != before:
        geocode_locations.save_cache(cache_path, cache)
    ok = sum(1 for e in enriched if e.get("geocode_status") == "ok")
    print(f"# geocode-locations: {ok}/{len(enriched)} resolved (cache entries: {len(cache)})",
          file=sys.stderr)
    _print_json(enriched)
    return 0


def cmd_map_match_events(args) -> int:
    events = json.loads(Path(args.input).read_text(encoding="utf-8"))
    store_path = Path(args.stores) if args.stores else \
        config.REPO_ROOT / "DATA" / "s03_primary" / "store.tsv"
    if not store_path.exists():
        raise SystemExit(f"Missing store file: {store_path} — run synthesize-stores first.")
    stores = map_match_events.load_stores(store_path)
    matches = map_match_events.match_events_to_stores(events, stores, radius_m=args.radius_m)
    print(f"# map_match_events: {len(matches)} store-event pairs within {args.radius_m:.0f}m "
          f"({len(stores)} stores, {len(events)} events)", file=sys.stderr)
    _print_json(matches)
    return 0


def cmd_present_messages(args) -> int:
    data = Path(args.input).read_text(encoding="utf-8")
    print(present_messages.present(data, fmt=args.format, language=args.language, style=args.style))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract-events", help="extract structured events from search results")
    pe.add_argument("--input", default=None, help="searched_events.tsv (default: DATA/s08_search/…)")
    pe.add_argument("--location", default=None, help="filter to this location (default: first in file)")
    pe.add_argument("--limit-items", type=int, default=None, help="cap result rows fed to the model")
    pe.add_argument("--raw", action="store_true", help="print the model's unparsed reply (debug)")
    pe.set_defaults(func=cmd_extract_events)

    pa = sub.add_parser("estimate-attendance", help="estimate attendance for events JSON")
    pa.add_argument("--input", required=True, help="events JSON (extract-events's output)")
    pa.add_argument("--raw", action="store_true", help="print the model's unparsed reply (debug)")
    pa.set_defaults(func=cmd_estimate_attendance)

    pg = sub.add_parser("geocode-locations", help="add lat/lon to events via Google Geocoding")
    pg.add_argument("--input", required=True, help="events JSON (extract-events's output)")
    pg.add_argument("--api-key", default=None, help="override GOOGLE_GEOCODING_API_KEY")
    pg.add_argument("--language", default=config.GEOCODE_LANGUAGE, help="result language bias")
    pg.add_argument("--region", default=config.GEOCODE_REGION, help="ccTLD region bias")
    pg.add_argument("--cache", default=None, help=f"cache file (default: {config.GEOCODE_CACHE})")
    pg.add_argument("--no-cache", action="store_true", help="disable the persistent cache")
    pg.set_defaults(func=cmd_geocode_locations)

    pm = sub.add_parser("map-match-events", help="associate events with stores within 200m")
    pm.add_argument("--input", required=True, help="geocoded events JSON (geocode-locations's output)")
    pm.add_argument("--stores", default=None, help="store.tsv (default: DATA/s03_primary/store.tsv)")
    pm.add_argument("--radius-m", type=float, default=map_match_events.EVENT_RADIUS_M,
                    help="match radius in metres (default: 200, per Sales.md)")
    pm.set_defaults(func=cmd_map_match_events)

    pp = sub.add_parser("present-messages", help="render structured data as a natural-language message")
    pp.add_argument("--input", required=True, help="data file (JSON or TSV)")
    pp.add_argument("--format", default="json", choices=["json", "tsv"])
    pp.add_argument("--language", default="ja", choices=["ja", "en"])
    pp.add_argument("--style", default="brief", choices=["brief", "detailed", "bullet"])
    pp.set_defaults(func=cmd_present_messages)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
