---
name: calibrate-for-events-map-match
description: >-
  Step 3 of 4 of the event sales-calibration chain (extract → geo-code → map-match →
  estimate-attendance), per docs/pipeline/calibration/Calibrate-for-events-map-match.md. Reads
  the geocoded events in DATA/s09_calibration/geocoded_events.json and the store network in
  DATA/s03_primary/store.tsv, and keeps every (store, event) pair whose haversine distance is
  within --radius-m (default 500 m), tagging each with distance_m and an active flag (1 if the
  event runs tomorrow, JST). Writes DATA/s09_calibration/mapped_events.json, sorted by
  store_name then distance_m. Deterministic; no LLM, no network, no cost. Use to associate
  real events with the Ichimaru stores near them.
---

# Calibrate for events — map-match

**Step 3 of 4** of the event side of sales calibration. Associates each geocoded event with
the Ichimaru stores near it. Full spec:
[Calibrate-for-events-map-match.md](../../../docs/pipeline/calibration/Calibrate-for-events-map-match.md).

```
extract ─► geo-code ─► map-match (this) ─► estimate-attendance
```

**Deterministic step.** No LLM, no network, no cost — pure spatial matching.

## Pipeline

```
geo-code                haversine match within --radius-m (+ store.tsv)
  geocoded_events.json ─────────────────────────────────────►  mapped_events.json
```

Imports the agent-service task module `agent.tasks.map_match_events` (stdlib-only,
torch-free) so matching agrees with the API **and** with `synthesize-sales`' haversine
(`docs/pipeline/synthetics/Sales.md` §"Map distance algorithm", `EARTH_RADIUS_M = 6371000`).
Events without usable coordinates (geocode misses) are skipped.

## What it produces

`DATA/s09_calibration/mapped_events.json` — a UTF-8 JSON **list**, one object per
(store, event) pair within `--radius-m`, sorted by `store_name` then `distance_m`:

- `prefecture`, `store_name` — the matched store
- `event_name`, `event_type`, `start_date`, `end_date`, `active` (1 if the event runs
  **tomorrow**, JST; half-open ranges count on the known bound's side, unknown ranges count
  as active)
- `venue`, `location`, `latitude`, `longitude` — the event's geocoded position
- `distance_m` — store-to-event distance, rounded to 0.1 m
- `source_url`

An empty list is a valid result (no geocoded event within `--radius-m` of any store) — the
next step then writes an empty list too, without calling the API.

> **Why 500 m?** The default is deliberately wider than the 200 m event-influence radius in
> `Sales.md`: real, web-found events are geocoded to venues (sometimes only approximately), so
> calibration casts a wider net and keeps `distance_m` for downstream weighting or tightening.

## Inputs

- `DATA/s09_calibration/geocoded_events.json` — from `calibrate-for-events-geo-code`.
- `DATA/s03_primary/store.tsv` — from `synthesize-stores`.
- An **installed agent-service** (for the importable `agent.tasks.map_match_events` module —
  the server need **not** be running).

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/calibrate-for-events-map-match/scripts/map_match_events.py
# widen/narrow the match radius:
python skills/calibrate-for-events-map-match/scripts/map_match_events.py --radius-m 300
```

Options: `--repo-root`, `--radius-m` (default 500). Re-running overwrites the output.

## Notes & maintenance

- **Deterministic** for a fixed JST date (the `active` flag depends on "tomorrow"); no cost.
- Raising `--radius-m` above 500 m makes the `distance_loss` `other` band reachable in the
  next step (see its config).
- **Upstream:** [calibrate-for-events-geo-code](../calibrate-for-events-geo-code/SKILL.md) and
  `synthesize-stores`.
- **Downstream:**
  [calibrate-for-events-estimate-attendance](../calibrate-for-events-estimate-attendance/SKILL.md)
  consumes `mapped_events.json`.
