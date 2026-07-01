---
name: search-events
description: >-
  Web-search near-future public events (祭り・花火大会・イベント・コンサート) around
  each Ichimaru location via the Tavily API, per docs/search/Search-events.md. Reduces
  each store to prefecture + 市区町村 (dropping 政令市の行政区, keeping 東京特別区),
  searches once per distinct location for the coming week, and writes
  DATA/s08_search/searched_events.tsv (one row per result) plus a raw JSONL. Live /
  not reproducible. Use to retrieve real event signals for a location.
---

# Search events

Retrieves near-future public events around each Ichimaru location with the Tavily
search API, following
[docs/search/Search-events.md](../../../docs/search/Search-events.md). **Live step**:
results depend on when it is run (not reproducible), and it makes ~one paid Tavily
"advanced" search per distinct location (~45 today).

## What it produces

In `DATA/s08_search/` (created if missing):

- `searched_events.tsv` — one row per search **result**: `location`, `query`,
  `fetched_at`, `title`, `url`, `content`, `score`, `published_date`. Tabs/newlines in
  text fields are collapsed to spaces so each result stays on one line.
- `searched_events_raw.jsonl` — one JSON object per **location** with the full Tavily
  response (for full fidelity / re-parsing).
- `searched_events_errors.tsv` — only if some locations failed (`location`, `query`,
  `error`).

`location` is the join key: a consumer maps it back to stores by running the same
extraction over `store.tsv`.

## Location extraction

Per store: strip the `prefecture` prefix, then take up to and including the **first
`市`** (searching from index 1, so a name-initial 市 like 市原市/市川市 is not mistaken
for the boundary); else the first `区` (Tokyo special ward); else the first `町`/`村`.
Prepend the prefecture. This drops 政令市の行政区 (they follow the 市) while keeping
東京特別区 (`東京都世田谷区` stays `東京都世田谷区`, not `東京都`).

## Secret & dependencies

- `TAVILY_API_KEY` is read from the environment or the repo-root `.env` (git-ignored)
  and passed as `TavilyClient(api_key=...)`. It is **never printed**; a missing key is
  a clear hard error.
- Requires `tavily-python` (pinned in `requirements.txt`). `pip install -r
  requirements.txt` inside the `.venv`.

## How to run it

```bash
source .venv/bin/activate
pip install -r requirements.txt
# preview locations + queries WITHOUT calling the API (no key/credits needed):
python ai/skills/search-events/scripts/search_events.py --dry-run
# real run (uses Tavily credits):
python ai/skills/search-events/scripts/search_events.py
```

Options: `--repo-root <path>`, `--today YYYY-MM-DD` (override the window's "today",
JST), `--max-results N` (default 10), `--limit-locations N` (cap searches for
testing/cost), `--dry-run`. `make search` runs this skill (after the weather forecast).

## How it works

- **Window.** The query targets the coming week (today+1 .. today+7, JST). Tavily
  filters by *publish* date, not *event* date, so the date window is encoded in the
  query text; the downstream agent parses actual event dates from the results.
- **Query.** One Japanese query per location, e.g.
  `…で2026年7月（07/02〜07/08）に開催されるイベント・祭り・花火大会・コンサート・マルシェ`,
  called with `search_depth="advanced"`, `topic="general"`, `max_results`,
  `country="japan"` (auto-dropped on older SDKs that lack those kwargs).
- **Robustness.** Each location is retried up to 3× with backoff; a final failure is
  recorded and skipped so one bad location never aborts the batch.

## Notes & maintenance

- **Not reproducible / costs credits** — unlike the deterministic pipeline skills.
  Use `--dry-run` and `--limit-locations` while developing.
- Only *retrieves*; extracting dated, geolocated, sized events from the text is a
  separate consuming agent's job (see Search-events.md → "Downstream use").
- Constants (`WINDOW_DAYS`, `MAX_RESULTS`, `RETRIES`, `OUT_COLS`) are at the top of
  [scripts/search_events.py](scripts/search_events.py).
