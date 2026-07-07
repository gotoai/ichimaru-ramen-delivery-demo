## Calibrate for events — step 3: map-match

**Step 3 of 4** of the event side of sales calibration
([overview](Calibrate-for-events.md)): extract → geo-code → map-match →
estimate-attendance. Associate each geocoded event with the Ichimaru stores near it — a
**deterministic** spatial step (no LLM, no network, no cost), the analogue of the synthetic
event influence in `docs/pipeline/synthetics/Sales.md`.

Skill: `pipeline/skills/calibrate-for-events-map-match/`.

### Prerequisites

  - An **installed agent-service**: this step imports the task module
    `agent-service/agent/tasks/map_match_events.py` (stdlib-only, torch-free) so matching
    agrees with the API and with `synthesize-sales`. The server need **not** be running.

### Inputs

  - `DATA/s09_calibration/geocoded_events.json` — the geocode step's output.
  - `DATA/s03_primary/store.tsv` — the store network (`prefecture`, `store_name`,
    `latitude`, `longitude`, …), the map-match target.

### Map-match (`agent-service/agent/tasks/map_match_events.py`)

Read the stores from `store.tsv`, and keep every (store, event) pair whose haversine
great-circle distance (`docs/pipeline/synthetics/Sales.md` §"Map distance algorithm",
`EARTH_RADIUS_M = 6371000`) is within **`radius_m` — default 500 m**. Events without usable
coordinates are skipped. Each match carries an `active` flag: 1 if the event's date range
covers **tomorrow (JST)** — half-open ranges count on the known bound's side, unknown ranges
count as active.

> The 500 m default is deliberately wider than the 200 m event-influence radius in
> `Sales.md`: real, web-found events are geocoded to venues (sometimes only
> approximately), so calibration casts a wider net and keeps `distance_m` in the output
> for downstream weighting or tightening. `radius_m` is a parameter (`--radius-m`).

**Output — `DATA/s09_calibration/mapped_events.json`**: a UTF-8 JSON **list**, one
object per (store, event) match, sorted by `store_name` then `distance_m`:

  - `prefecture`, `store_name` — the matched store
  - `event_name`, `event_type`, `start_date`, `end_date`, `active`
  - `venue`, `location`, `latitude`, `longitude` — the event's geocoded position
  - `distance_m` — store-to-event distance, rounded to 0.1 m
  - `source_url`

An empty list is a valid result (no geocoded event within `radius_m` of any store) — in
that case the estimate step writes an empty list too, without calling the API.

### Notes

  - **Deterministic** for a fixed JST date (the `active` flag depends on "tomorrow"); no
    LLM, no network, no cost.
  - Raising `--radius-m` above 500 m makes the estimate step's `distance_loss` `other` band
    reachable (see its config).
  - **Upstream:** [Calibrate-for-events-geo-code.md](Calibrate-for-events-geo-code.md) and
    `synthesize-stores` (`store.tsv`).
  - **Downstream:**
    [Calibrate-for-events-estimate-attendance.md](Calibrate-for-events-estimate-attendance.md)
    estimates demand for each pair in `mapped_events.json`.
