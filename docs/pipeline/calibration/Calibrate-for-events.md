## Calibrate for events — overview

Turn the raw web-search results from `search-events` into **per-store demand uplifts from
real events**. The work is split into **four independent steps**, each its own skill and
guideline, reading the previous step's output file so any one can be re-run alone:

```
search-events
  searched_events.tsv
        │  extract        agent-service /v1/extract-events                    (LLM; live/GPU)
        ▼
  extracted_events.json
        │  geo-code       Google Geocoding (venue + location)                 (deterministic; paid)
        ▼
  geocoded_events.json
        │  map-match      haversine within radius_m (+ store.tsv)             (deterministic; free)
        ▼
  mapped_events.json
        │  estimate       /v1/estimate-attendance + demand rules             (LLM; live/GPU)
        ▼
  estimated_events.json  ──►  Calibrate-merge.md  (applies the uplift to predicted_sales)
```

All outputs are UTF-8 JSON **lists** under `DATA/s09_calibration/`. The event-side analogue
of the weather calibration's forecast join; the final `estimated_events.json` feeds
[Calibrate-merge.md](Calibrate-merge.md).

### The four steps

| # | Guideline | Skill | In → Out | Cost |
|---|-----------|-------|----------|------|
| 1 | [Calibrate-for-events-extract.md](Calibrate-for-events-extract.md) | `calibrate-for-events-extract` | `searched_events.tsv` → `extracted_events.json` | LLM / GPU |
| 2 | [Calibrate-for-events-geo-code.md](Calibrate-for-events-geo-code.md) | `calibrate-for-events-geo-code` | `extracted_events.json` → `geocoded_events.json` | Google Geocoding |
| 3 | [Calibrate-for-events-map-match.md](Calibrate-for-events-map-match.md) | `calibrate-for-events-map-match` | `geocoded_events.json` + `store.tsv` → `mapped_events.json` | free |
| 4 | [Calibrate-for-events-estimate-attendance.md](Calibrate-for-events-estimate-attendance.md) | `calibrate-for-events-estimate-attendance` | `mapped_events.json` → `estimated_events.json` | LLM / GPU |

Steps 1 and 4 need the agent-service web API running (`make serve` in `agent-service/`, wait
for `/readyz`); steps 2 and 3 import the agent-service task modules (stdlib-only, torch-free)
but don't need the server. See each guideline for its prerequisites, I/O schema, and notes.

### Why four steps

Splitting the chain lets each stage be re-run independently against the previous output:
re-tune the demand rules in `config.yaml` and re-run only **estimate**; widen the match radius
and re-run only **map-match**; re-geocode without paying for extraction again. It also keeps
each cost boundary explicit — the two LLM/GPU steps, the one paid geocoding step, and the free
deterministic match are separate skills you can run (and preview) on their own.

### Notes

  - **Live / not reproducible; spends money.** Steps 1 and 4 run LLM generations on the local
    GPU (slow); step 2 bills Google Geocoding per cache-missed query. There is no `--dry-run`;
    preview by limiting locations (`--limit-locations` on the extract step).
  - **Upstream:** `search-events` (the TSV) and `synthesize-stores` (`store.tsv`); the
    agent-service must be installed per `agent-service/INSTALL.md`.
  - **Downstream:** [Calibrate-merge.md](Calibrate-merge.md) consumes `estimated_events.json`
    to adjust `predicted_sales`, analogous to `Calibrate-for-weather.md`.
