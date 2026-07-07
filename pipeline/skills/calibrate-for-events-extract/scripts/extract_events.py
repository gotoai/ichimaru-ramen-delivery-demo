#!/usr/bin/env python3
"""Calibrate for events — step 1/4: extract clean structured events (LLM; live/GPU).

Implements docs/pipeline/calibration/Calibrate-for-events-extract.md.

  DATA/s08_search/searched_events.tsv
    -> POST {agent-service}/v1/extract-events (one call per distinct location)
    -> DATA/s09_calibration/extracted_events.json

Groups the search results by distinct `location` and makes one extract-events call per
location (the same batching the search used), tagging each returned event with the
`search_location` it came from. Duplicates across nearby locations are kept — the
downstream map-match makes them harmless.

Live / not reproducible; spends GPU time. The agent-service web API must be running
(`make serve` in agent-service/, wait for /readyz). Reads GOTOAI_AGENT_API_KEY (bearer)
from the environment or agent-service/.env; never logs it. This is the first of four
independent event-calibration skills:

  extract (this) -> geo-code -> map-match -> estimate-attendance
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

EXTRACT_ITEMS_DEFAULT = 8       # results fed to the model per location (see Search-events)
API_TIMEOUT_DEFAULT = 300.0     # first request triggers a lazy model load on the server


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


# --------------------------------------------------------------------- HTTP to the API
class ApiError(Exception):
    """Retryable API failure (non-2xx other than auth, or a transport hiccup)."""


def api_post(base_url: str, path: str, body: dict, *, api_key: str, timeout: float) -> dict:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise SystemExit("401 from agent-service — set GOTOAI_AGENT_API_KEY to match "
                             "the server (agent-service/.env).")
        raise ApiError(f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            raise SystemExit(f"Cannot reach agent-service at {base_url} — start it with "
                             "`make serve` in agent-service/ and wait for /readyz.")
        raise ApiError(str(exc))


def api_post_retry(base_url, path, body, *, api_key, timeout, label, retries=1):
    """POST with one retry; on final failure log and return None (retry-and-skip)."""
    for attempt in range(retries + 1):
        try:
            return api_post(base_url, path, body, api_key=api_key, timeout=timeout)
        except ApiError as exc:
            if attempt >= retries:
                print(f"  [skip] {label}: {exc}", file=sys.stderr)
                return None
            time.sleep(2 * (attempt + 1))


# ----------------------------------------------------------------------------- helpers
def read_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def write_json_list(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------- extract
def extract_events(repo, base_url, api_key, *, limit_items, limit_locations, timeout, out_path):
    tsv = repo / "DATA" / "s08_search" / "searched_events.tsv"
    if not tsv.exists():
        raise SystemExit(f"Missing input: {tsv} — run the search-events skill first.")
    rows = read_tsv(tsv)

    groups: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        groups.setdefault(r.get("location", ""), []).append(r)
    locations = [loc for loc in groups if loc]
    if limit_locations:
        locations = locations[:limit_locations]

    events: list[dict] = []
    for loc in locations:
        items = [{"title": r.get("title", ""), "content": r.get("content", ""),
                  "url": r.get("url", ""), "published_date": r.get("published_date", "")}
                 for r in groups[loc][:limit_items]]
        data = api_post_retry(base_url, "/v1/extract-events",
                              {"location": loc, "items": items},
                              api_key=api_key, timeout=timeout, label=f"extract {loc}")
        if data is None:
            continue
        found = data.get("events", [])
        for ev in found:
            ev = dict(ev)
            ev["search_location"] = loc
            events.append(ev)
        print(f"  {loc}: {len(found)} events")

    write_json_list(out_path, events)
    print(f"Wrote {out_path} ({len(events)} events from {len(locations)} locations).")
    return events


# ---------------------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--base-url", default=None, help="agent-service base URL (default: from .env / :8000)")
    ap.add_argument("--limit-items", type=int, default=EXTRACT_ITEMS_DEFAULT,
                    help="cap search results fed to the model per location")
    ap.add_argument("--limit-locations", type=int, default=None, help="cap locations (testing/cost)")
    ap.add_argument("--timeout", type=float, default=API_TIMEOUT_DEFAULT, help="API request timeout (s)")
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    env_path = repo / "agent-service" / ".env"

    base_url = args.base_url or (
        f"http://{read_env_value(env_path, 'API_HOST', '127.0.0.1')}"
        f":{read_env_value(env_path, 'API_PORT', '8000')}")
    api_key = read_env_value(env_path, "GOTOAI_AGENT_API_KEY")

    out_path = repo / "DATA" / "s09_calibration" / "extracted_events.json"
    extract_events(repo, base_url, api_key, limit_items=args.limit_items,
                   limit_locations=args.limit_locations, timeout=args.timeout,
                   out_path=out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
