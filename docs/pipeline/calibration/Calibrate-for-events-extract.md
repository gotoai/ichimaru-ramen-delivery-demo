## Calibrate for events — step 1: extract events

**Step 1 of 4** of the event side of sales calibration
([overview](Calibrate-for-events.md)): extract → geo-code → map-match →
estimate-attendance. Turn the raw web-search results from `search-events` into clean,
structured events via the agent-service web API (an LLM step). Downstream steps geocode
those events, match them to nearby stores, and estimate the demand they add.

Skill: `pipeline/skills/calibrate-for-events-extract/`.

### Prerequisites

  - **The agent-service web API must be running** (it hosts the Gemma model used here).
    From `agent-service/`: `make serve` (single worker; loads the model at startup), then
    wait until `GET /readyz` returns 200. Base URL defaults to `http://127.0.0.1:8000`
    (`API_HOST`/`API_PORT` in `agent-service/.env`).
  - If `GOTOAI_AGENT_API_KEY` is set in `agent-service/.env`, every `/v1` call must send
    `Authorization: Bearer <key>`. Read the key from that `.env`; never log it.

### Input

  - `DATA/s08_search/searched_events.tsv` — the `search-events` output, one row per web
    search result: `location`, `query`, `fetched_at`, `title`, `url`, `content`,
    `score`, `published_date`.

### Extract events (agent-service `/v1/extract-events`)

Group the TSV rows by **distinct `location`** (~45) and make **one API call per
location** — the same batching the search used, so the model sees each area's results
together and the `対象地域` hint in the prompt matches the rows.

For each location, `POST {base_url}/v1/extract-events` with a JSON body mirroring
`agent-service/tests/test_api_client.py`:

```json
{
  "location": "千葉県八千代市",
  "items": [
    {"title": "…", "content": "…", "url": "…", "published_date": "…"}
  ]
}
```

  - `items` carries the location's rows (`title`/`content`/`url`/`published_date` map
    1:1 from the TSV columns). Cap items per request (default **8**, as in the CLI
    spike) — the server truncates each `content` to 600 chars, but many long items
    still slow the small model down.
  - Use a **generous timeout (≥ 300 s)** and call **sequentially**: the server holds a
    GPU lock and serves one generation at a time; parallel calls only queue.
  - Per-location failures are non-fatal (retry once, then skip and record the location
    in the run log), matching `search-events`' retry-and-skip behavior.

The response is `{events, location, item_count, event_count, prompt_version,
model_id}`; each event has `event_name`, `event_type` (one of `concert|festival|
fireworks|market|sports|exhibition|other`), `start_date`/`end_date` (`YYYY-MM-DD` or
`""`), `location`, `venue`, `source_url`, `confidence` (0–1). Concatenate all
locations' `events` into one list, adding to each event:

  - `search_location` — the TSV `location` the event was extracted from (provenance;
    the model's own `location` field may be finer- or coarser-grained).

**Output — `DATA/s09_calibration/extracted_events.json`**: a UTF-8 JSON **list** of
those event objects (create the directory if absent). Events may legitimately repeat
across nearby search locations; do not deduplicate here — the map-match step makes
duplicates harmless and deduplication would need a fuzzy name match this demo avoids.

### Notes

  - **Live / not reproducible.** Runs LLM generations on the local GPU (slow: expect
    ~0.5–1 min per request). There is no `--dry-run` equivalent — preview by limiting
    locations (`--limit-locations`).
  - **LLM quality is best-effort.** The small model may miss events or emit
    low-`confidence` ones; `confidence` is carried through so downstream steps can filter
    (this step does not).
  - **Upstream:** `search-events` (the TSV); the agent-service must be installed per
    `agent-service/INSTALL.md`.
  - **Downstream:** [Calibrate-for-events-geo-code.md](Calibrate-for-events-geo-code.md)
    geocodes `extracted_events.json`.
