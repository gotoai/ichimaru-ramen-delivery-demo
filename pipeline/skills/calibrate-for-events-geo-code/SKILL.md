---
name: calibrate-for-events-geo-code
description: >-
  Step 2 of 4 of the event sales-calibration chain (extract → geo-code → map-match →
  estimate-attendance), per docs/pipeline/calibration/Calibrate-for-events-geo-code.md.
  Resolves each event in DATA/s09_calibration/extracted_events.json to coordinates via the
  Google Geocoding API (venue + location, components=country:JP), adding latitude/longitude,
  formatted_address, location_type, geocode_confidence, place_id, geocode_status, and writes
  DATA/s09_calibration/geocoded_events.json. Uses a persistent query cache so repeated venues
  aren't billed twice. Deterministic geocoder (no LLM) but live/paid; not reproducible. Use to
  give extracted events map coordinates for store matching.
---

# Calibrate for events — geo-code

**Step 2 of 4** of the event side of sales calibration. Resolves each extracted event to
coordinates so the next step can match it to nearby stores. Full spec:
[Calibrate-for-events-geo-code.md](../../../docs/pipeline/calibration/Calibrate-for-events-geo-code.md).

```
extract ─► geo-code (this) ─► map-match ─► estimate-attendance
```

**Live / paid step.** No LLM (a geocoder is authoritative; LLMs hallucinate coordinates), but
it bills Google Geocoding per **cache-missed** query, and live geocodes vary — not
reproducible.

## Pipeline

```
extract                Google Geocoding (venue + location, components=country:JP)
  extracted_events.json ──────────────────────────────────────►  geocoded_events.json
```

Imports the agent-service task module `agent.tasks.geocode_locations` (stdlib-only,
torch-free) so this skill geocodes **identically to the API**. For each event it queries
`venue + location` (venue first, `components=country:JP`, language `ja`, region `jp`).

## What it produces

`DATA/s09_calibration/geocoded_events.json` — a UTF-8 JSON **list**: every event from
`extracted_events.json`, unchanged, plus geocode fields: `latitude`, `longitude`,
`formatted_address`, `location_type`, `geocode_confidence` (ROOFTOP 1.0 → APPROXIMATE 0.4),
`place_id`, and `geocode_status`. Failures are **per-event and non-fatal**: an unresolved
event keeps empty coordinates and a `geocode_status` note (`zero-results`,
`no-venue-or-location`, `error: …`) — the next step skips events without usable coordinates.

## Caching

A persistent query→result cache at `agent-service/.cache/geocode_cache.json` is read and
updated in place, so re-runs and repeated venues (across runs) aren't billed twice. Cache
misses (`zero-results`) are cached too, so dead strings aren't re-queried.

## Inputs & secrets

- `DATA/s09_calibration/extracted_events.json` — from `calibrate-for-events-extract`.
- **`GOOGLE_GEOCODING_API_KEY`** — read from the environment or `agent-service/.env`;
  **never logged**. Required; missing key is a hard error.
- An **installed agent-service** (for the importable `agent.tasks.geocode_locations` module —
  the server itself need **not** be running for this step).

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/calibrate-for-events-geo-code/scripts/geocode_events.py
```

Options: `--repo-root`. Re-running overwrites the output (but re-uses the geocode cache).

## Notes & maintenance

- **Live / paid; not reproducible.** Only cache-missed queries bill Google.
- **Best-effort quality.** `geocode_confidence` is carried through so downstream steps can
  filter approximate hits (this skill does not).
- **Upstream:** [calibrate-for-events-extract](../calibrate-for-events-extract/SKILL.md).
- **Downstream:** [calibrate-for-events-map-match](../calibrate-for-events-map-match/SKILL.md)
  consumes `geocoded_events.json`.
