# agent-service

Gemma 4 12B agent for the Ichimaru demo. It consumes the pipeline's outputs
(`DATA/…`, notably `DATA/s08_search/searched_events.tsv`) and runs small LLM tasks:

1. **extract-events** — search text → structured events (name, dates, location, type)
2. **geocode-locations** — event venue/location → coordinates (Google Geocoding; not an LLM step)
3. **map-match-events** — associate events with stores within 200 m (haversine; not an LLM step)
4. **estimate-attendance** — event → estimated crowd size (range + confidence)
5. **present-messages** — structured data (TSV/JSON) → a natural-language message

This directory is **self-contained** and has its **own venv and dependencies**
(torch/transformers/bitsandbytes) — kept separate from the CPU-installable data
pipeline. It reads the pipeline's `DATA/` by path; there is no shared Python env.

Beyond the per-task calls above, the service also hosts an **agentic analytics assistant**
(`agent/analyst.py`): a tool-using loop that answers demand/forecast/accuracy questions by
querying the pipeline's `DATA/s10_analysis/` DuckDB layer with SQL (`run_sql`) and a
sandboxed shell (`run_bash`), iterating tool calls until it produces an answer. It backs the
`POST /v1/chat` endpoint and the interactive REPL (`agent/chatbot.py`).

> Status: the LLM client, the task modules, the analytics agent, and a CLI/REPL to exercise
> them are in place, and the web API (`agent.api`) serves the tasks end-to-end:
> `extract-events`, `estimate-attendance`, `present-messages`, `validate-message`, `chat`,
> and `chat/stream` (plus `/healthz` and `/readyz`).

## Setup (CUDA GPU box)

See **[INSTALL.md](INSTALL.md)** for the full, ordered bring-up (GPU driver check, venv,
the CUDA-matched PyTorch wheel + fallback, `pip install -r requirements.txt`,
`.env`/`HF_HOME`, and the smoke test). In brief:

```bash
cd agent-service
python3 -m venv .venv && source .venv/bin/activate && pip install -U pip
pip install torch torchvision          # CUDA build — see INSTALL.md if cuda.is_available() is False
pip install -r requirements.txt
cp .env.example .env                    # edit HF_HOME (shared cache) / MODEL_ID / GOTOAI_AGENT_API_KEY
python tests/smoke_test.py             # model loads + one reply + peak VRAM (~9-11GB in 4-bit)
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

## Run the web API

The API is a thin wrapper over the task modules. Run a **single worker** — the model is a
process-wide singleton in VRAM, so multiple workers would load multiple copies. Only one
generation runs at a time (a lock serialises GPU access); extra requests queue.

```bash
cd agent-service && source .venv/bin/activate
python -m agent.api                      # host/port + GOTOAI_AGENT_API_KEY from .env
# or explicitly: uvicorn agent.api:app --host 127.0.0.1 --port 8000
```

Endpoints:

- `GET /healthz` — liveness (process up; model not required).
- `GET /readyz` — readiness (model loaded; 503 until then). With `API_EAGER_LOAD=1` the
  model loads at startup.
- `POST /v1/extract-events` — search results (in the request body) → structured events.
- `POST /v1/estimate-attendance` — events (extract-events's output shape) → crowd-size
  estimates (`expected_attendance` range + confidence + rationale); optional `context`
  hints.
- `POST /v1/present-messages` — structured data → a natural-language message.
- `POST /v1/validate-message` — check a rendered message against its structured source.
- `POST /v1/chat` — the analytics agent: a multi-step SQL/bash tool loop over the
  `s10_analysis` layer that returns a grounded answer (non-streaming — a tool loop can't stream).
- `POST /v1/chat/stream` — token-streamed chat (SSE) for the web-app's chat panel.

`/v1` endpoints require `Authorization: Bearer $GOTOAI_AGENT_API_KEY` when a key is set.
Interactive docs at `/docs`.

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/extract-events \
  -H "Authorization: Bearer $GOTOAI_AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"location":"東京都世田谷区","items":[
        {"title":"世田谷区民まつり","content":"...","url":"https://example.com/a","published_date":"2026-06-30"}
      ]}'
```

The endpoint takes `items` in the body (decoupled from `DATA/` paths) — build them from
`searched_events.tsv` the same way the CLI does, or from any source.

## Layout

```
agent-service/
  requirements.txt        # torch/transformers/bitsandbytes — isolated from the pipeline
  .env.example            # MODEL_ID / HF_HOME / MAX_NEW_TOKENS / GOTOAI_AGENT_API_KEY
  agent/
    config.py             # loads .env BEFORE torch; model + generation settings
    llm.py                # Gemma 4 12B 4-bit client (load once; generate/chat/generate_tools)
    analyst.py            # agentic analytics loop (SQL/bash tools over s10_analysis) — torch-free
    tasks/
      extract_events.py   # search text -> structured events
      geocode_locations.py # event venue/location -> coordinates (Google Geocoding)
      map_match_events.py # events -> stores within 200m (haversine, per Sales.md)
      estimate_attendance.py # events -> crowd-size estimates
      present_messages.py # structured data -> natural-language message
      validate.py         # check a rendered message against its structured source
      chat.py             # chat prompt assembly for the streaming endpoint
      _jsonio.py          # tolerant JSON extraction from model output
    tools/
      sql_tool.py         # run_sql: DuckDB query over the s10_analysis views
      bash_tool.py        # run_bash: sandboxed shell (no network, read-only /data)
    api.py                # web API (FastAPI): /v1/{extract-events,estimate-attendance,
                          #   present-messages,validate-message,chat,chat/stream} + /healthz,/readyz
    cli.py                # exercise the tasks (python -m agent.cli ...)
    chatbot.py            # interactive REPL over the analytics agent (python -m agent.chatbot)
  tests/smoke_test.py     # model load + one reply + VRAM
```

## Notes

- **Structured output** relies on tolerant JSON parsing today; the next hardening step
  is **constrained/guided decoding** (JSON-schema/grammar) — small models often need it.
- Task modules keep a pure `build_messages` / `parse_*` pair (torch-free, unit-testable
  offline) separate from the LLM call, so they can be evaluated and later wrapped as
  `/v1/...` API endpoints without change.
- `GOTOAI_AGENT_API_KEY` in `.env` is the bearer key for the web API's `/v1` endpoints; the CLI doesn't use it.
