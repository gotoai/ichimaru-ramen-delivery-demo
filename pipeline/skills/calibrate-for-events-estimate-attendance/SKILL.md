---
name: calibrate-for-events-estimate-attendance
description: >-
  Step 4 of 4 of the event sales-calibration chain (extract → geo-code → map-match →
  estimate-attendance), per
  docs/pipeline/calibration/Calibrate-for-events-estimate-attendance.md. Reduces the
  (store, event) pairs in DATA/s09_calibration/mapped_events.json to distinct events, calls the
  agent-service /v1/estimate-attendance API to estimate each event's attendance, then converts
  attendance into expected extra bowls per (store, event) via configurable rules
  (baseline_demand_probability × distance_loss, capped at max_added_demand) and writes
  DATA/s09_calibration/estimated_events.json. Live / not reproducible; needs the agent-service
  running (GPU). Use to turn matched events into per-store demand uplifts.
---

# Calibrate for events — estimate-attendance

**Step 4 of 4** (final) of the event side of sales calibration. Estimates how many people
each matched event draws, then converts that into an expected **extra demand in bowls** per
(store, event) pair. The event-side analogue of
[calibrate-for-weather](../calibrate-for-weather/SKILL.md). Full spec:
[Calibrate-for-events-estimate-attendance.md](../../../docs/pipeline/calibration/Calibrate-for-events-estimate-attendance.md).

```
extract ─► geo-code ─► map-match ─► estimate-attendance (this)
```

**Live step.** Runs LLM generations on the agent-service GPU. Results depend on the current
data and are not reproducible.

## Pipeline

```
map-match              agent-service /v1/estimate-attendance + demand rules
  mapped_events.json ──────────────────────────────────────►  estimated_events.json
```

Reduces the pairs to **distinct events by `event_name`** (one estimate reused across nearby
stores), calls `POST /v1/estimate-attendance` in **chunks of 8**, joins the estimates back by
name, and applies the demand rules. Sequential; per-chunk failures retry once then skip.

## What it produces

`DATA/s09_calibration/estimated_events.json` — a UTF-8 JSON **list**, one object per
(store, event) pair of `mapped_events.json`, same order: every mapped field plus
`expected_attendance` (`{point, low, high}`), `attendance_confidence`, `rationale`,
`attendance_used`, the applied `demand_probability` and `distance_loss`, `estimated_demand`
(bowls), `demand_capped`, and `estimate_status` (`ok` / `no-estimate`).

### Demand formula

```
estimated_demand = min( round( attendance × baseline_demand_probability × distance_loss ),
                        max_added_demand )
```

- `attendance` is the estimate's `point`, or the `low`/`high` midpoint if `point` is blank;
  if no number is available the pair is `no-estimate` with demand 0.
- `baseline_demand_probability` is chosen by **keyword-matching** the config keys against the
  event's `event_type` **and** `event_name` (case-insensitive substring, config order): e.g.
  `festival` matches "music festival" and `祭り` matches "音楽祭り". English keys (`festival`,
  `fireworks`, `sports`) tend to hit the enum `event_type`; Japanese keys (`花火`, `祭り`,
  `大会`, …) hit the Japanese `event_name`. `other` is the fallback and is never matched as a
  substring.
- `distance_loss` is the first band whose upper bound ≥ `distance_m` (else `other`).
- The cap is **per (store, event) pair**; summing per store/date is a later step's job.

Worked example: a fireworks event with 35 000 attendees, a store 80 m away →
`35000 × 0.02 × 0.2 = 140` → capped to **20** bowls (`demand_capped = true`).

## Configuration

The demand rules live in `pipeline/config/config.yaml` under `calibration/events/`:

```yaml
calibration:
  events:
    baseline_demand_probability:      # P(attendee buys a bowl); keys keyword-matched
      fireworks: 0.02                 # against event_type + event_name (substring)
      花火: 0.02
      festival: 0.02
      祭り: 0.02
      sports: 0.01
      大会: 0.01
      other: 0.01                     # fallback when no key matches
    distance_loss:                    # distance-decay factor; key = band upper bound (m)
      100: 0.2
      200: 0.1
      500: 0.05
      other: 0.03
    max_added_demand: 20              # cap (bowls) per (store, event) pair
```

Re-tune these and re-run **only this skill** (the earlier steps' outputs are unchanged).

## Inputs & secrets

- `DATA/s09_calibration/mapped_events.json` — from `calibrate-for-events-map-match`.
- **agent-service running.** From `agent-service/`: `make serve`, then wait until
  `GET /readyz` returns 200. Base URL defaults to `http://127.0.0.1:8000`
  (`API_HOST`/`API_PORT` in `agent-service/.env`); override with `--base-url`.
- **`GOTOAI_AGENT_API_KEY`** (bearer) — read from the environment or `agent-service/.env`;
  **never logged**. A 401 is a hard error.

## How to run it

From `pipeline/`, with the project `.venv` active (needs `PyYAML`, already pinned):

```bash
source .venv/bin/activate
# 0) in another terminal: cd ../agent-service && make serve   (wait for /readyz)

python skills/calibrate-for-events-estimate-attendance/scripts/estimate_attendance.py
```

Options: `--repo-root`, `--config` (default `pipeline/config/config.yaml`), `--base-url`,
`--timeout` (default 300 s — the first request pays the model load). Re-running overwrites the
output.

## Notes & maintenance

- **Live / not reproducible.** LLM attendance guesses vary.
- **The demand rules are demo heuristics.** `max_added_demand` guards the whole chain from a
  runaway LLM attendance guess. Tune in `config.yaml`, not in code.
- **Upstream:** [calibrate-for-events-map-match](../calibrate-for-events-map-match/SKILL.md).
- **Downstream:** [calibrate-merge](../calibrate-merge/SKILL.md) consumes
  `estimated_events.json` (per-store, per-event demand with `active` + the date range) to
  adjust `predicted_sales`.
