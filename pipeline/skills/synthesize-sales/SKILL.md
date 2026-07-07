---
name: synthesize-sales
description: >-
  Synthesize daily store sales (bowls of ramen) for the Ichimaru demo per the
  algorithm in docs/pipeline/synthetics/Sales.md. For every store in
  DATA/s03_primary/store.tsv and every date in the sales_history window, builds a
  weekday/weekend baseline and adds influences from nearby home buildings,
  competitors, events, temperature and rain, then rounds and clamps (>= 10).
  Writes DATA/s03_primary/sales.tsv. Use when asked to synthesize, generate, or
  refresh the demo's historical sales.
---

# Synthesize sales

Generates the demo's **actual daily sales** per store, following
[docs/pipeline/synthetics/Sales.md](../../../docs/pipeline/synthetics/Sales.md). One row per
(store, date) over the `sales_history` time horizon.

## What it produces

`DATA/s03_primary/sales.tsv` — UTF-8, tab-separated, columns:
`prefecture`, `store_name`, `date` (YYYY-MM-DD), `sales` (integer bowls, >= 10).

For the default horizon this is **80 stores × ~910 days ≈ 72,800 rows** and runs
in ~30 seconds.

## Inputs

- `pipeline/config/config.yaml` — `synthetics/random_seed`, the `synthetics/sales/*`
  sampling specs, and `time_horizon/sales_history` (the date range).
- `DATA/s03_primary/store.tsv` — stores (location + weekday/weekend baselines).
- `DATA/s03_primary/{home_building,competitor,event}.tsv` — influence sources.
- `DATA/s02_intermediate/weather-station.tsv` — station coordinates.
- `DATA/s02_intermediate/weather_history_*.tsv` — daily temperature & rain
  (combined `_all_` files, or the per-prefecture fallback files).

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/synthesize-sales/scripts/synthesize_sales.py
```

Options: `--start YYYY-MM-DD` / `--end YYYY-MM-DD` (override the auto-computed
period), `--limit-stores N` (testing), `--repo-root <path>`.

## How it works

Per (store, date) the sales value is built up (see Sales.md for the exact
formulas), then `round`-ed and clamped to a minimum of 10:

1. **Baseline** — weekday/weekend baseline from `store.tsv` × beta scale ×
   gaussian noise.
2. **Home buildings** within 500 m and active on the date: `+ unit/10 · Sh ·
   100/(100+Dh) · Nh`.
3. **Competitors** within 50 m and active: `− Bc/4 · Sc · 10/(10+Dc) · Nc`.
4. **Events** within 200 m whose `event_date` is the date: `+ people/20 · Se ·
   50/(50+De) · Ne`.
5. **Temperature** of the nearest usable station: `× (1 + (20−Th)·0.02·St) · Nt`.
6. **Rain** of the same station: `× (30/(30+Vr)·Sr) · Nr`.

Map distance is the haversine (Earth radius 6,371,000 m).

**Reproducibility.** All draws come from one RNG seeded with
`synthetics/random_seed` (629), consumed in a fixed order (stores by
`store_name`, dates ascending, entities in file order). `scale_sampling` (beta,
`normalize: yes`) is divided by its mean so its expectation is 1.0;
`noise_sampling` (gaussian) is clipped to `bounds`. Output is byte-identical
across runs.

**Weather join.** Station coordinates live in `weather-station.tsv` and values in
the `weather_history_*.tsv` files; they are matched by station name on a
best-effort basis (unmatched observation stations are dropped — no coordinates).
The nearest matched station to a store supplies `最高気温(℃)` (Th) and
`降水量の合計(mm)` (Vr); a missing day is filled forward up to 3 prior days, and
if still missing that weather factor is skipped (left at 1.0).

## Notes & maintenance

- **Dependency:** PyYAML (for `config.yaml`). Sampling uses the stdlib `random`
  (`betavariate`, `gauss`); everything else is standard library.
- The influence radii (500/50/200 m), the weather columns, and the min-sales
  clamp are constants at the top of
  [scripts/synthesize_sales.py](scripts/synthesize_sales.py).
- Upstream dependency: this skill consumes `DATA/s03_primary/{store,home_building,
  competitor,event}.tsv` (the store/POI/event synthesis skills) and the
  `weather_history_*.tsv` from `retrieve-weather-history`; run those first.
