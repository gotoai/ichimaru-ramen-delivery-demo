## Calibrate for events — step 4: estimate attendance and demand

**Step 4 of 4** (final) of the event side of sales calibration
([overview](Calibrate-for-events.md)): extract → geo-code → map-match →
estimate-attendance. Estimate how many people each matched event draws (an LLM step via
the agent-service), then convert attendance into an **expected extra demand in bowls** per
(store, event) pair by configurable rules. Applying the demand uplift to `predicted_sales`
is the separate `Calibrate-merge.md` step.

Skill: `pipeline/skills/calibrate-for-events-estimate-attendance/`.

### Prerequisites

  - **The agent-service web API must be running** (it hosts the Gemma model used here).
    From `agent-service/`: `make serve`, then wait until `GET /readyz` returns 200.
  - If `GOTOAI_AGENT_API_KEY` is set in `agent-service/.env`, every `/v1` call must send
    `Authorization: Bearer <key>`. Read the key from that `.env`; never log it.

### Input

  - `DATA/s09_calibration/mapped_events.json` — the map-match step's output (one row per
    (store, event) pair).

### Attendance (`POST {base_url}/v1/estimate-attendance`)

`mapped_events.json` has one row per (store, event) pair, so first reduce it to **distinct
events by `event_name`** (first occurrence wins) — the same event near several stores must
be estimated once, and the API's estimates join back by `event_name`. Call the API in
**chunks of ≤ 8 events** per request (larger listings risk truncating the JSON reply), body
per `agent-service/tests/test_api_client.py`:

```json
{
  "context": {"地域": "…"},
  "events": [
    {"event_name": "…", "event_type": "…", "start_date": "…", "venue": "…", "location": "…"}
  ]
}
```

Each estimate carries `expected_attendance` (`{point, low, high}` — integers, or `""`
when the model omitted one), `confidence` and `rationale`. The attendance used
downstream is `point`; if `point` is blank fall back to the `low`/`high` midpoint; if no
number is available the pair keeps `estimate_status = "no-estimate"` and an
`estimated_demand` of 0. The same sequential-call / generous-timeout / retry-once rules
as the extract step apply.

### Demand rules

Configurable in `pipeline/config/config.yaml` under `calibration/events/`, defaults:

```yaml
calibration:
  events:
    baseline_demand_probability:   # P(attendee buys a bowl); keys keyword-matched
      fireworks: 0.02              # against event_type + event_name (substring)
      花火: 0.02
      festival: 0.02
      祭り: 0.02
      sports: 0.01
      大会: 0.01
      other: 0.01                  # fallback when no key matches
    distance_loss:                 # distance-decay factor, by store-event distance band
      100: 0.2                     # 0 < distance_m ≤ 100
      200: 0.1                     # 100 < distance_m ≤ 200
      500: 0.05                    # 200 < distance_m ≤ 500
      other: 0.03                  # beyond the last band (only reachable if radius_m > 500)
    max_added_demand: 20           # cap, in bowls, per (store, event) pair
```

**Formula**, per (store, event) pair:

```
estimated_demand = min(round(attendance × baseline_demand_probability × distance_loss),
                       max_added_demand)
```

  - `baseline_demand_probability` is chosen by **keyword-matching** the config keys, in
    order, against the pair's `event_type` and `event_name` combined (case-insensitive
    substring): e.g. `festival` matches "music festival" and `祭り` matches "音楽祭り".
    The English enum keys tend to hit `event_type`, the Japanese keys the `event_name`.
    When no key matches, the `other` value is used (`other` is never matched as a substring).
  - `distance_loss` is looked up by the pair's `distance_m`: the factor of the first
    band whose upper bound is ≥ `distance_m` (bands sorted ascending), else `other`.
  - Round to the nearest integer **before** capping; record `demand_capped = true` when
    the cap bit. Worked example: 花火大会, point 35 000, store at 80 m →
    `35000 × 0.02 × 0.2 = 140` → capped to **20** bowls.
  - The cap is **per pair**: a store near several concurrent events can exceed
    `max_added_demand` in total. Summing pairs per store per date — and any re-capping —
    is the applying (`Calibrate-merge.md`) step's decision, not this one's.

**Output — `DATA/s09_calibration/estimated_events.json`**: a UTF-8 JSON **list**, one
object per (store, event) pair of `mapped_events.json`, same order, each carrying:

  - all fields of the `mapped_events.json` entry (store identity, event descriptors,
    `active`, `distance_m`, `source_url`, …)
  - `expected_attendance` (`{point, low, high}`), `attendance_confidence`, `rationale`
    — from the API (blank on `no-estimate` pairs)
  - `attendance_used` — the number the formula consumed (`point`, or the midpoint
    fallback; `""` when unavailable)
  - `demand_probability`, `distance_loss` — the applied factors
  - `estimated_demand` (integer bowls), `demand_capped` (bool)
  - `estimate_status` — `"ok"` or `"no-estimate"`

If `mapped_events.json` is empty, write an empty list without calling the API.

### Notes

  - **Live / not reproducible.** Runs LLM generations on the local GPU (slow: ~0.5–1 min
    per request); LLM attendance guesses vary. Re-tune the rules and re-run **only this
    step** — the earlier steps' outputs are unchanged.
  - **The demand rules are demo heuristics.** `baseline_demand_probability` ×
    `distance_loss` treats "attendee buys a bowl" and "attendee passes the store" as
    independent, and `max_added_demand` guards the whole chain (LLM attendance guess included)
    from blowing up a store's forecast. Tune them in `config.yaml`, not in code.
  - **LLM quality is best-effort.** `attendance_confidence` is carried through so downstream
    steps can filter (this step does not).
  - **Upstream:** [Calibrate-for-events-map-match.md](Calibrate-for-events-map-match.md); the
    agent-service must be running.
  - **Downstream:** [Calibrate-merge.md](Calibrate-merge.md) consumes
    `estimated_events.json` (per-store, per-event demand uplifts with `active` and the event
    date range) to adjust `predicted_sales`, analogous to `Calibrate-for-weather.md`.
