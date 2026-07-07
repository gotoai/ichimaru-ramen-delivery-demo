---
name: calibrate-for-events-extract
description: >-
  Step 1 of 4 of the event sales-calibration chain (extract → geo-code → map-match →
  estimate-attendance), per docs/pipeline/calibration/Calibrate-for-events-extract.md. Calls
  the agent-service web API to extract clean, structured events from the search-events output
  DATA/s08_search/searched_events.tsv — one /v1/extract-events call per distinct location —
  and writes DATA/s09_calibration/extracted_events.json (event_name, event_type,
  start_date/end_date, location, venue, source_url, confidence; each tagged with its
  search_location). Live / not reproducible; needs the agent-service running (GPU). Use to
  turn raw event search text into structured events for geocoding.
---

# Calibrate for events — extract

**Step 1 of 4** of the event side of sales calibration. Turns the raw web-search text from
[search-events](../search-events/SKILL.md) into clean, structured event records using the
agent-service LLM. Full spec:
[Calibrate-for-events-extract.md](../../../docs/pipeline/calibration/Calibrate-for-events-extract.md).

```
extract (this) ─► geo-code ─► map-match ─► estimate-attendance
```

**Live step.** Runs LLM generations on the agent-service GPU. Results depend on the current
search data and are not reproducible. Preview cheaply with `--limit-locations`.

## Pipeline

```
search-events                 agent-service /v1/extract-events
  searched_events.tsv ──────────────────────────────────────►  extracted_events.json
```

Groups `searched_events.tsv` by distinct `location` (~45) and makes **one
`POST /v1/extract-events` per location** (≤ `--limit-items` results each, default 8), so the
model sees each area's results together. Sequential (the server serves one GPU generation at
a time); per-location failures retry once then skip.

## What it produces

`DATA/s09_calibration/extracted_events.json` — a UTF-8 JSON **list** of structured events
(`event_name`, `event_type` ∈ `concert|festival|fireworks|market|sports|exhibition|other`,
`start_date`/`end_date`, `location`, `venue`, `source_url`, `confidence`), each tagged with
the `search_location` it came from. Duplicates across nearby search locations are kept — the
downstream `map-match` makes them harmless, and deduplication would need a fuzzy name match
this demo avoids.

## Inputs & secrets

- `DATA/s08_search/searched_events.tsv` — from `search-events`.
- **agent-service running.** From `agent-service/`: `make serve`, then wait until
  `GET /readyz` returns 200. Base URL defaults to `http://127.0.0.1:8000`
  (`API_HOST`/`API_PORT` in `agent-service/.env`); override with `--base-url`.
- **`GOTOAI_AGENT_API_KEY`** (bearer) — read from the environment or `agent-service/.env`;
  **never logged**. If the server has a key set, all `/v1` calls send it; a 401 is a hard error.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
# 0) in another terminal: cd ../agent-service && make serve   (wait for /readyz)

python skills/calibrate-for-events-extract/scripts/extract_events.py
# preview a few locations:
python skills/calibrate-for-events-extract/scripts/extract_events.py --limit-locations 3
```

Options: `--repo-root`, `--base-url`, `--limit-items` (results per location, default 8),
`--limit-locations N` (cap for testing/cost), `--timeout` (default 300 s — the first request
pays the model load). Re-running overwrites the output.

## Notes & maintenance

- **Live / not reproducible.** LLM output varies; preview with `--limit-locations`.
- **Best-effort quality.** The small model may miss events; `confidence` is carried through
  for downstream filtering (this skill does not filter).
- **Upstream:** `search-events`, and an installed agent-service (`agent-service/INSTALL.md`).
- **Downstream:** [calibrate-for-events-geo-code](../calibrate-for-events-geo-code/SKILL.md)
  consumes `extracted_events.json`.
