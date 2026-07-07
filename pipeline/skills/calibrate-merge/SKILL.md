---
name: calibrate-merge
description: >-
  Merge the weather and event calibrations into the final week+1 calibrated sales, per
  docs/pipeline/calibration/Calibrate-merge.md. The weather calibration is a multiplicative
  correction and the event calibration is additive extra bowls, so calibrated_sales =
  weather_calibrated_sales + event_added_demand. Sums each store's nearby active events
  (re-checking each event's date range against the prediction's target_date, de-duplicating
  by event name) onto the weather-calibrated row. Writes DATA/s09_calibration/
  calibrated_sales.tsv (one row per prediction) and calibration_info.json (per-row
  breakdown of both calibrations). Stdlib-only, deterministic. Use to get the final
  weather- and event-adjusted week+1 forecast with an auditable explanation.
---

# Merge weather and event calibrations

Combines the two independent calibrations of the model's week+1 sales, following
[Calibrate-merge.md](../../../docs/pipeline/calibration/Calibrate-merge.md):

- **weather** ([calibrate-for-weather](../calibrate-for-weather/SKILL.md)) — a
  *multiplicative* bias / temperature / rainfall correction.
- **events** ([calibrate-for-events-estimate-attendance](../calibrate-for-events-estimate-attendance/SKILL.md),
  the final step of the extract → geo-code → map-match → estimate-attendance chain) — an
  *additive* extra demand in bowls from real upcoming events near each store.

Different scales, so the merge adds the event bowls on top of the weather ratio:

```
calibrated_sales = weather_calibrated_sales + event_added_demand
```

The weather-calibrated rows are the spine (one per prediction); event demand is summed
onto the stores and target dates where events apply, and is 0 everywhere else.

Unlike the two upstream calibration skills, this step is **deterministic and reproducible**
— it only merges files (no API, no network, no paid calls).

## What it produces

Under `DATA/s09_calibration/`, one row/object per prediction, in the same order as
`weather_calibrated_sales.tsv`:

### `calibrated_sales.tsv` (UTF-8 TSV, header row)

- `prefecture`, `store_name`, `reference_date`, `target_date`
- `predicted_sales` — the raw model float (carried through)
- `weather_calibrated_sales` — the weather-calibrated value
- `event_added_demand` — summed extra bowls (integer; 0 when no event applies)
- `event_count` — number of distinct events contributing on that date
- `calibrated_sales` — `weather_calibrated_sales + event_added_demand`

### `calibration_info.json` (a JSON list, one object per prediction)

```json
{
  "prefecture": "千葉県",
  "store_name": "千葉県八千代市勝田台南店",
  "reference_date": "2026-06-25",
  "target_date": "2026-06-29",
  "predicted_sales": 158.83,
  "weather_calibrated_sales": 152.11,
  "event_added_demand": 25,
  "calibrated_sales": 177.11,
  "formula": "calibrated = weather_calibrated + event_added_demand",
  "weather": {
    "weather_applied": true,
    "inputs": {"feature_high_temperature": 31.6, "forecast_high_temperature": 25.0,
               "temp_gap": 6.6, "forecast_rainfall": 12.0},
    "factors": [
      {"type": "bias", "band": null, "slope": 0.961, "applied": true, "reason": "…"},
      {"type": "temperature", "band": "above_5to10", "slope": 0.94, "applied": true, "reason": "…"}
    ]
  },
  "events": [
    {"event_name": "…", "event_type": "festival", "start_date": "2026-06-29",
     "end_date": "2026-06-29", "distance_m": 90.0, "estimated_demand": 5, "source_url": "…"}
  ],
  "self_check_ok": true
}
```

`weather` is copied from `weather_calibration_info.json` (`inputs` is `null` on bias-only
rows); `events` lists only the events that contribute on that `target_date`;
`self_check_ok` confirms `weather_calibrated_sales + event_added_demand` reproduces
`calibrated_sales`.

## Resolving events to target dates

`estimated_events.json` is per (store, event), so the merge maps each event onto the
prediction dates it covers:

- **Activity is recomputed per `target_date`** from the event's `start_date`/`end_date`
  (**not** the stored `active` flag, which is relative to *tomorrow* only): both dates →
  `start ≤ target ≤ end`; only `start` → `target ≥ start`; only `end` → `target ≤ end`;
  neither → active on every date. Same rule as `map_match_events.is_active`.
- **De-duplicated by `(store_name, event_name)`**, keeping the nearest occurrence, so an
  event surfaced from several search locations counts once.
- Pairs with `estimated_demand ≤ 0` (`no-estimate`) drop out.

**No per-store re-cap** — each event term is already capped at `max_added_demand` per
pair; the sum is left uncapped (a per-store cap could be added later).

## Inputs

Under `DATA/s09_calibration/` (run both calibration skills first):

- `weather_calibrated_sales.tsv` + `weather_calibration_info.json` — from
  `calibrate-for-weather`.
- `estimated_events.json` — from the event chain's final step,
  `calibrate-for-events-estimate-attendance`. **Optional at runtime:** if it is absent or
  empty, every `event_added_demand` is 0 (the merge degrades to the weather calibration) and
  the run warns.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/calibrate-merge/scripts/calibrate_merge.py
```

Stdlib only (no third-party deps); deterministic for fixed inputs. Option: `--repo-root`
(auto-detected). Re-running overwrites both outputs. The run prints the split of rows with
an event uplift vs. weather-only.

## Notes & maintenance

- **Additive vs. multiplicative is deliberate:** weather is a proportional correction of
  the baseline; an event is extra foot traffic that adds bowls largely independent of the
  baseline, so it is added after the weather ratio rather than multiplied in.
- **Coverage is sparse:** events are rare in space and time, so `calibrated_sales` equals
  `weather_calibrated_sales` on almost every row.
- **Not wired into `make calibration`:** it depends on `estimated_events.json`, whose
  producing chain (`calibrate-for-events-extract` → `-geo-code` → `-map-match` →
  `-estimate-attendance`) needs the agent-service running. Run this after both upstream
  calibrations.
- **Upstream:** `calibrate-for-weather` and the `calibrate-for-events-*` chain.
- **Downstream:** `calibrated_sales.tsv` is the final week+1 calibrated forecast for
  delivery planning; `calibration_info.json` explains every adjustment.
