#!/usr/bin/env python3
"""Web-search near-future public events around each Ichimaru location (Tavily).

Implements docs/pipeline/search/Search-events.md. Live step (not reproducible):

  1. Read DATA/s03_primary/store.tsv; derive the distinct search locations by
     reducing each store_name to prefecture + 市区町村 (dropping 政令市の行政区 but
     keeping 東京特別区): strip the prefecture prefix, then take up to and including
     the first 市; else the first 区 (Tokyo special ward); else the first 町/村.
  2. For each distinct location, query the Tavily search API for events in the
     coming week (window encoded in the query text; Tavily filters by publish date,
     not event date). Per-location try/except with retries; failures are recorded and
     skipped, never aborting the batch.
  3. Write DATA/s08_search/searched_events.tsv (one row per result), a companion
     searched_events_raw.jsonl (full responses), and searched_events_errors.tsv
     (only if any location failed).

The TAVILY_API_KEY is read from the environment or pipeline/.env (never logged).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))

OUT_COLS = ["location", "query", "fetched_at", "title", "url", "content", "score", "published_date"]
WINDOW_DAYS = 7            # search the coming week (today+1 .. today+7)
MAX_RESULTS = 10
RETRIES = 3


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline" / "search" / "Search-events.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/pipeline/search/Search-events.md not found).")


def read_tsv(path: Path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def load_api_key(repo_root: Path) -> str:
    """TAVILY_API_KEY from the environment, else parsed from pipeline/.env."""
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        env = repo_root / "pipeline" / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "TAVILY_API_KEY":
                    key = v.strip().strip('"').strip("'")
                    break
    if not key:
        raise SystemExit("TAVILY_API_KEY not found in the environment or pipeline/.env — cannot search.")
    return key


def extract_location(prefecture: str, store_name: str) -> str:
    """prefecture + 市区町村, dropping 政令市の行政区 but keeping 東京特別区.

    Strip the prefecture prefix, then take up to and including the first 市; else the
    first 区 (Tokyo special ward); else the first 町/村.
    """
    rest = store_name[len(prefecture):] if store_name.startswith(prefecture) else store_name
    # Search from index 1: the boundary is the suffix that *ends* the 市区町村, so a
    # municipality-name-initial 市 (市原市, 市川市) must not be treated as the boundary.
    i = rest.find("市", 1)
    if i != -1:
        return prefecture + rest[: i + 1]
    i = rest.find("区", 1)
    if i != -1:
        return prefecture + rest[: i + 1]
    cands = [k for k in (rest.find("町", 1), rest.find("村", 1)) if k != -1]
    if cands:
        return prefecture + rest[: min(cands) + 1]
    return prefecture + rest            # fallback (not expected)


def distinct_locations(stores) -> list[str]:
    seen, out = set(), []
    for s in stores:
        loc = extract_location(s["prefecture"], s["store_name"])
        if loc not in seen:
            seen.add(loc)
            out.append(loc)
    return sorted(out)


def build_query(location: str, start: dt.date, end: dt.date) -> str:
    return (f"{location}で{start.year}年{start.month}月"
            f"（{start:%m/%d}〜{end:%m/%d}）に開催されるイベント・祭り・花火大会・"
            f"コンサート・マルシェ")


def tavily_search(client, query: str, max_results: int):
    """One Tavily 'advanced' search, dropping unsupported kwargs on older SDKs."""
    kwargs = dict(query=query, search_depth="advanced", topic="general",
                  max_results=max_results, include_answer=False, country="japan")
    try:
        return client.search(**kwargs)
    except TypeError:                    # older SDK without country/topic
        kwargs.pop("country", None)
        kwargs.pop("topic", None)
        return client.search(**kwargs)


def clean(v) -> str:
    """One-line TSV-safe string: collapse tabs/newlines to spaces."""
    return " ".join(str("" if v is None else v).split())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD, JST)")
    ap.add_argument("--max-results", type=int, default=MAX_RESULTS)
    ap.add_argument("--limit-locations", type=int, default=None, help="cap locations (testing/cost)")
    ap.add_argument("--dry-run", action="store_true", help="print locations+queries, no API calls")
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    stores = read_tsv(repo / "DATA" / "s03_primary" / "store.tsv")
    locations = distinct_locations(stores)
    if args.limit_locations:
        locations = locations[: args.limit_locations]

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.datetime.now(JST).date())
    start, end = today + dt.timedelta(days=1), today + dt.timedelta(days=WINDOW_DAYS)
    print(f"Today (JST): {today} | window: {start}..{end} | locations: {len(locations)}")

    if args.dry_run:
        for loc in locations:
            print(f"  {loc}\t{build_query(loc, start, end)}")
        print("(dry run — no API calls, no files written)")
        return 0

    from tavily import TavilyClient
    client = TavilyClient(api_key=load_api_key(repo))

    out_dir = repo / "DATA" / "s08_search"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, raw_records, errors = [], [], []

    for loc in locations:
        query = build_query(loc, start, end)
        fetched_at = dt.datetime.now(JST).isoformat(timespec="seconds")
        resp = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = tavily_search(client, query, args.max_results)
                break
            except Exception as exc:                      # noqa: BLE001 - keep batch alive
                if attempt >= RETRIES:
                    errors.append({"location": loc, "query": query,
                                   "error": f"{type(exc).__name__}: {exc}"})
                    print(f"  [FAIL] {loc}: {type(exc).__name__}: {exc}", file=sys.stderr)
                else:
                    time.sleep(2 * attempt)
        if resp is None:
            continue
        raw_records.append({"location": loc, "query": query, "fetched_at": fetched_at,
                            "response": resp})
        results = resp.get("results", []) if isinstance(resp, dict) else []
        for r in results:
            rows.append({
                "location": loc, "query": query, "fetched_at": fetched_at,
                "title": clean(r.get("title")), "url": clean(r.get("url")),
                "content": clean(r.get("content")), "score": r.get("score", ""),
                "published_date": clean(r.get("published_date", "")),
            })
        print(f"  {loc}: {len(results)} results")

    out_path = out_dir / "searched_events.tsv"
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS, delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    with open(out_dir / "searched_events_raw.jsonl", "w", encoding="utf-8") as f:
        for rec in raw_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if errors:
        with open(out_dir / "searched_events_errors.tsv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["location", "query", "error"], delimiter="\t")
            w.writeheader()
            w.writerows(errors)

    print(f"Wrote {len(rows)} results from {len(raw_records)}/{len(locations)} locations "
          f"to {out_path}" + (f" ({len(errors)} failed)" if errors else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
