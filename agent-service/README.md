# agent-service

Gemma 4 E4B agent for the Ichimaru demo. It consumes the pipeline's outputs
(`DATA/…`, notably `DATA/s08_search/searched_events.tsv`) and runs small LLM tasks:

1. **extract-events** — search text → structured events (name, dates, location, type)
2. **geocode-locations** — event venue/location → coordinates (Google Geocoding; not an LLM step)
3. **map-match-events** — associate events with stores within 200 m (haversine; not an LLM step)
4. **estimate-attendance** — event → estimated crowd size (range + confidence)
5. **present-messages** — structured data (TSV/JSON) → a natural-language message

This directory is **self-contained** and has its **own venv and dependencies**
(torch/transformers/bitsandbytes) — kept separate from the CPU-installable data
pipeline. It reads the pipeline's `DATA/` by path; there is no shared Python env.

> Status: **basic parts only** — the LLM client, the three task modules, and a CLI to
> exercise them. There is **no web API yet**: the plan is to measure the small model's
> quality/latency first (the "spike"), then design the `/v1` endpoints and SLA around
> what actually works.

## Setup (CUDA GPU box)

See **[INSTALL.md](INSTALL.md)** for the full, ordered bring-up (GPU driver check, venv,
the CUDA-matched PyTorch wheel + fallback, `pip install -r requirements.txt`,
`.env`/`HF_HOME`, and the smoke test). In brief:

```bash
cd agent-service
python3 -m venv .venv && source .venv/bin/activate && pip install -U pip
pip install torch torchvision          # CUDA build — see INSTALL.md if cuda.is_available() is False
pip install -r requirements.txt
cp .env.example .env                    # edit HF_HOME (shared cache) / MODEL_ID / AGENT_API_KEY
python tests/smoke_test.py             # model loads + one reply + peak VRAM (~5-7GB in 4-bit)
```

## Run the tasks (spike)

```bash
# 1) extract events for one location from the search output
python -m agent.cli extract-events --location 東京都世田谷区 --limit-items 8 > events.json

# 2) add coordinates to each event (needs GOOGLE_GEOCODING_API_KEY in .env)
python -m agent.cli geocode-locations --input events.json > events_geo.json

# 3) associate events with stores within 200 m (reads DATA/s03_primary/store.tsv)
python -m agent.cli map-match-events --input events_geo.json

# 4) estimate attendance for those events
python -m agent.cli estimate-attendance --input events_geo.json

# 5) render any structured data as prose
python -m agent.cli present-messages --input events.json --style bullet
```

## Layout

```
agent-service/
  requirements.txt        # torch/transformers/bitsandbytes — isolated from the pipeline
  .env.example            # MODEL_ID / HF_HOME / MAX_NEW_TOKENS / AGENT_API_KEY
  agent/
    config.py             # loads .env BEFORE torch; model + generation settings
    llm.py                # Gemma 4 E4B 4-bit client (load once; generate/chat)
    tasks/
      extract_events.py   # search text -> structured events
      geocode_locations.py # event venue/location -> coordinates (Google Geocoding)
      map_match_events.py # events -> stores within 200m (haversine, per Sales.md)
      estimate_attendance.py # events -> crowd-size estimates
      present_messages.py # structured data -> natural-language message
      _jsonio.py          # tolerant JSON extraction from model output
    cli.py                # exercise the tasks (python -m agent.cli ...)
  tests/smoke_test.py     # model load + one reply + VRAM
```

## Notes

- **Structured output** relies on tolerant JSON parsing today; the next hardening step
  is **constrained/guided decoding** (JSON-schema/grammar) — small models often need it.
- Task modules keep a pure `build_messages` / `parse_*` pair (torch-free, unit-testable
  offline) separate from the LLM call, so they can be evaluated and later wrapped as
  `/v1/...` API endpoints without change.
- `AGENT_API_KEY` in `.env` is for the future web API (bearer auth); the CLI doesn't use it.
