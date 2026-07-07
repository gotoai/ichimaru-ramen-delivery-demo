## Merge weather and event calibrations

Combine the two independent calibrations of the Demand Forecast Model's week+1 sales into
one final number per prediction:

  - **weather** (`calibrate-for-weather`) — a *multiplicative* correction that undoes the
    model's diagnosed bias and its temperature/rainfall errors, per
    `docs/pipeline/calibration/Calibrate-for-weather.md`.
  - **events** (the `calibrate-for-events-*` chain: extract → geo-code → map-match →
    estimate-attendance) — an *additive* extra demand in bowls from real upcoming events
    near each store, per `docs/pipeline/calibration/Calibrate-for-events.md` (overview) and
    its four step guidelines.

The two act on different scales (a ratio vs. a count of extra bowls), so the merge is:

```
calibrated_sales = weather_calibrated_sales + event_added_demand
```

The weather-calibrated number is the row spine — **every** prediction gets one — and the
event demand is added on top for the stores and dates where events apply (0 everywhere
else).

### Inputs

All under `DATA/s09_calibration/` (run both calibration skills first):

  - `weather_calibrated_sales.tsv` — one row per prediction: `prefecture`, `store_name`,
    `reference_date`, `target_date`, `predicted_sales`, `calibrated_sales` (the
    weather-calibrated value used here as `weather_calibrated_sales`), `weather_applied`,
    and the weather factor columns.
  - `weather_calibration_info.json` — the per-prediction weather factor breakdown
    (`inputs`, `factors`), joined into the explanation output by
    `(store_name, reference_date, target_date)`.
  - `estimated_events.json` — one object per (store, event) pair: `store_name`,
    `event_name`, `event_type`, `start_date`, `end_date`, `distance_m`,
    `estimated_demand`, `estimate_status`, `source_url`, …

### Event demand per (store, target_date)

`estimated_events.json` is per (store, event), not per date, so the merge resolves each
event to the prediction target dates it covers:

  - **Activity is recomputed against each `target_date`** from the event's
    `start_date`/`end_date` — **not** the stored `active` flag, which is relative to
    *tomorrow* (JST) only. An event contributes to a `target_date` T iff (same rule as
    `map_match_events.is_active`):
      - both dates known: `start ≤ T ≤ end`
      - only `start`: `T ≥ start`
      - only `end`: `T ≤ end`
      - neither (period unknown): contributes to every T
  - **De-duplicate by `(store_name, event_name)`** before summing. The same real event can
    be surfaced from several nearby search locations and matched to the same store more
    than once; keep the **nearest** occurrence (the first, since `estimated_events.json`
    is sorted by store then `distance_m`) so its demand is counted once.
  - Rows with `estimate_status != "ok"` carry `estimated_demand = 0` and drop out of the
    sum.

Then, for each prediction `(store, reference_date, target_date)`:

```
event_added_demand = Σ estimated_demand   over the distinct events active on target_date
calibrated_sales   = weather_calibrated_sales + event_added_demand
```

  - `event_added_demand` is a non-negative integer (each event's `estimated_demand` was
    already capped at `max_added_demand` per pair in `calibrate-for-events-estimate-attendance`).
  - **No per-store re-cap by default.** A store next to several concurrent events sums
    their contributions; the per-pair cap still bounds each term. An optional
    `calibration/events/max_added_demand_per_store` cap on the summed `event_added_demand`
    can be added later if a store's total needs bounding — it is intentionally left out
    here so the merge is a pure sum.

### Output

Two files under `DATA/s09_calibration/`, one row/object per prediction, in the same order
as `weather_calibrated_sales.tsv`.

**`calibrated_sales.tsv`** — UTF-8 TSV, header row. Floats rounded to 6 decimals.

  - `prefecture`, `store_name`, `reference_date`, `target_date`
  - `predicted_sales` — the raw model float (carried through)
  - `weather_calibrated_sales` — the weather-calibrated value (from the weather TSV)
  - `event_added_demand` — the summed extra bowls (integer; 0 when no event applies)
  - `event_count` — number of distinct events contributing on that date (0 or more)
  - `calibrated_sales` — `weather_calibrated_sales + event_added_demand`

**`calibration_info.json`** — a JSON **list**, one object per prediction, with the full
auditable breakdown of both calibrations:

  - `prefecture`, `store_name`, `reference_date`, `target_date`
  - `predicted_sales`, `weather_calibrated_sales`, `event_added_demand`, `calibrated_sales`
  - `formula` — the literal `"calibrated = weather_calibrated + event_added_demand"`
  - `weather` — `{weather_applied, inputs, factors}` copied from
    `weather_calibration_info.json` (the multiplicative bias/temperature/rainfall factors
    and their reasons; `inputs` is `null` on bias-only rows).
  - `events` — a list of the **contributing** events (active on this `target_date`,
    `estimated_demand > 0`, de-duplicated), each
    `{event_name, event_type, start_date, end_date, distance_m, estimated_demand,
    source_url}`. Empty list when no event applies.
  - `self_check_ok` — `true` when `weather_calibrated_sales + event_added_demand`
    reproduces `calibrated_sales` within `1e-6`.

### Notes

  - **Stdlib-only; deterministic** for fixed inputs (it only merges files — no API calls,
    no network). Unlike the two upstream calibration skills, this step is reproducible.
  - **Additive vs. multiplicative is deliberate.** Weather is a proportional correction of
    the baseline forecast; an event is extra foot traffic that adds bowls largely
    independent of the baseline, so it is added after the weather ratio is applied rather
    than multiplied in.
  - **Coverage.** Almost all rows have `event_added_demand = 0` — events are sparse in
    space and time — so `calibrated_sales` equals `weather_calibrated_sales` except near
    active events. The run should print the split (rows with vs. without an event uplift).
  - **Missing event input.** If `estimated_events.json` is absent or empty, treat every
    `event_added_demand` as 0 (the merge degrades to the weather calibration) and warn —
    it usually means the `calibrate-for-events-*` chain hasn't been run.
  - **Upstream:** `calibrate-for-weather` (the weather TSV + info JSON) and the
    `calibrate-for-events-*` chain (`estimated_events.json`). Run both first.
  - **Downstream:** `calibrated_sales.tsv` is the final week+1 calibrated forecast for
    delivery planning; `calibration_info.json` explains every adjustment for review.
