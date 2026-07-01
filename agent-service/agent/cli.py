"""CLI to exercise the tasks during the performance/quality spike (no web API yet).

    python -m agent.cli extract    [--input searched_events.tsv] [--location 東京都世田谷区] [--limit-items 10]
    python -m agent.cli attendance --input events.json
    python -m agent.cli present    --input events.json [--format json] [--language ja] [--style brief]

`extract` defaults to the pipeline's DATA/s08_search/searched_events.tsv.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import config
from .tasks import attendance, extract, present


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


def cmd_extract(args) -> int:
    path = Path(args.input) if args.input else config.REPO_ROOT / "DATA" / "s08_search" / "searched_events.tsv"
    if not path.exists():
        raise SystemExit(f"Missing input: {path} — run the search-events skill first.")
    location, items = _read_events_tsv(path, args.location, args.limit_items)
    print(f"# extract: location={location} items={len(items)}", file=sys.stderr)
    if args.raw:
        from .llm import get_llm
        print(get_llm().generate(extract.build_messages(items, location),
                                 do_sample=False, max_new_tokens=2048))
        return 0
    events = extract.extract_events(items, location)
    if not events:
        print("# extract: parsed 0 events — re-run with --raw to inspect the model output",
              file=sys.stderr)
    _print_json(events)
    return 0


def cmd_attendance(args) -> int:
    text = Path(args.input).read_text(encoding="utf-8").strip()
    try:
        events = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"{args.input} is not valid JSON ({exc}). It should contain only the events "
            "array from `extract`. Re-run: python -m agent.cli extract ... > <file>")
    if args.raw:
        from .llm import get_llm
        print(get_llm().generate(attendance.build_messages(events),
                                 do_sample=False, max_new_tokens=2048))
        return 0
    estimates = attendance.estimate_attendance(events)
    if not estimates:
        print("# attendance: parsed 0 estimates — re-run with --raw to inspect the model output",
              file=sys.stderr)
    _print_json(estimates)
    return 0


def cmd_present(args) -> int:
    data = Path(args.input).read_text(encoding="utf-8")
    print(present.present(data, fmt=args.format, language=args.language, style=args.style))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="extract structured events from search results")
    pe.add_argument("--input", default=None, help="searched_events.tsv (default: DATA/s08_search/…)")
    pe.add_argument("--location", default=None, help="filter to this location (default: first in file)")
    pe.add_argument("--limit-items", type=int, default=None, help="cap result rows fed to the model")
    pe.add_argument("--raw", action="store_true", help="print the model's unparsed reply (debug)")
    pe.set_defaults(func=cmd_extract)

    pa = sub.add_parser("attendance", help="estimate attendance for events JSON")
    pa.add_argument("--input", required=True, help="events JSON (extract's output)")
    pa.add_argument("--raw", action="store_true", help="print the model's unparsed reply (debug)")
    pa.set_defaults(func=cmd_attendance)

    pp = sub.add_parser("present", help="render structured data as a natural-language message")
    pp.add_argument("--input", required=True, help="data file (JSON or TSV)")
    pp.add_argument("--format", default="json", choices=["json", "tsv"])
    pp.add_argument("--language", default="ja", choices=["ja", "en"])
    pp.add_argument("--style", default="brief", choices=["brief", "detailed", "bullet"])
    pp.set_defaults(func=cmd_present)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
