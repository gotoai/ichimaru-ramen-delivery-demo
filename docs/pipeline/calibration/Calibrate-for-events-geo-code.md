## Calibrate for events — step 2: geocode

**Step 2 of 4** of the event side of sales calibration
([overview](Calibrate-for-events.md)): extract → geo-code → map-match →
estimate-attendance. Resolve each extracted event to coordinates so the map-match step
can associate it with nearby stores. A **deterministic enrichment** (not an LLM step —
LLMs hallucinate coordinates; a geocoder is authoritative).

Skill: `pipeline/skills/calibrate-for-events-geo-code/`.

### Prerequisites

  - `GOOGLE_GEOCODING_API_KEY` in `agent-service/.env` (or the environment) — required;
    never log it.
  - An **installed agent-service**: this step imports the task module
    `agent-service/agent/tasks/geocode_locations.py` (stdlib-only, torch-free) so it
    geocodes exactly as the API would. The server itself need **not** be running.

### Input

  - `DATA/s09_calibration/extracted_events.json` — the extract step's output.

### Geocode (`agent-service/agent/tasks/geocode_locations.py`)

For each extracted event, query the Google Geocoding API with `venue + location` (venue
first, `components=country:JP`, language `ja`, region `jp`) and add `latitude`,
`longitude`, `formatted_address`, `location_type`, `geocode_confidence` (ROOFTOP 1.0 →
APPROXIMATE 0.4), `place_id`, `geocode_status`. Use the persistent query cache at
`agent-service/.cache/geocode_cache.json` so re-runs and repeated venues aren't billed
twice (cache misses — `zero-results` — are cached too, so dead strings aren't re-queried).
Failures are per-event and non-fatal: unresolved events get empty coordinates and a
`geocode_status` note (`zero-results`, `no-venue-or-location`, `error: …`).

**Output — `DATA/s09_calibration/geocoded_events.json`**: a UTF-8 JSON **list** — every
event from `extracted_events.json`, unchanged, plus the geocode fields above. Row shape is
uniform (unresolved events carry empty coordinates), so the map-match step can iterate them
all and skip the ones without usable coordinates.

### Notes

  - **Live / paid; not reproducible.** Bills Google Geocoding per **cache-missed** query;
    live geocodes vary. No LLM and no GPU — the agent-service server need not be running.
  - **Best-effort quality.** `geocode_confidence` is carried through so downstream steps can
    filter approximate hits (this step does not).
  - **Upstream:** [Calibrate-for-events-extract.md](Calibrate-for-events-extract.md).
  - **Downstream:** [Calibrate-for-events-map-match.md](Calibrate-for-events-map-match.md)
    matches `geocoded_events.json` to stores.
