# Ichimaru Ramen Delivery Demo

A demonstration system for a **virtual food business, _Ichimaru Ramen_**, that plans
delivery/ordering operations across a network of stores in Japan. It showcases two
complementary AI capabilities end-to-end:

- a **predictive Machine-Learning pipeline** — synthesizes a realistic store network and
  sales history from open Japanese government data, trains a demand-forecast model, then
  predicts and **calibrates** next-week sales for weather and local events; and
- **analytic AI agents** — a locally-hosted Gemma model that turns raw search text into
  structured events, estimates crowd sizes, and answers area-managers' demand/forecast
  questions by querying a clean analytics layer with SQL (an agentic, tool-using loop).

Everything is **synthetic and for demonstration only** — no real Ichimaru business data is
used. The stores, competitors, homes, events, and sales are all generated from public data.

## Architecture

```
                     ┌───────────────────────────────────────────────┐
                     │  pipeline/   (CPU)                             │
  open gov data ───► │  base-data → synthetics → modeling →          │
  (e-Stat, JMA,      │  prediction → diagnosis → search →            │
   Tavily, Google)   │  calibration → analysis                       │
                     └───────────────┬───────────────────────────────┘
                                     │ writes
                              DATA/  │  (s01_raw … s10_analysis, TSV/JSON/PNG)
                                     │ read by
        ┌────────────────────────────┴───────────────────────────┐
        ▼                                                         ▼
┌───────────────────────────┐   proxies chat (SSE)   ┌───────────────────────────┐
│  web-app/    (CPU)         │ ─────────────────────► │  agent-service/  (CUDA GPU)│
│  area-manager dashboard    │                        │  Gemma 4 E4B host          │
│  map · chart · forecast    │ ◄───────────────────── │  /v1 tasks + analytics     │
│  table · per-day breakdown │      answers / prose   │  agent (SQL/bash tools)    │
└───────────────────────────┘                        └───────────────────────────┘
```

The three components are decoupled: the **pipeline** produces the `DATA/` tree; the
**web-app** and **agent-service** only read `DATA/` (the agent-service also reads the
`DATA/s10_analysis/` DuckDB layer). The web-app owns no model — it proxies chat to the
agent-service.

## Major components

| Component | Role | Host | Python env |
|---|---|---|---|
| [`pipeline/`](pipeline/) | Predictive ML pipeline: data retrieval, synthesis, demand-forecast modeling, prediction, diagnosis, live search, weather/event calibration, and a DuckDB-ready analytics layer. Driven by `make`. | CPU | `pipeline/.venv` |
| [`web-app/`](web-app/) | Human-facing **area-manager dashboard** (FastAPI + Jinja2 + HTMX). Shows calibrated sales on a map/chart/table with per-day breakdowns and an AI chat. Reads `DATA/`; proxies chat. | CPU | `web-app/.venv` |
| [`agent-service/`](agent-service/) | Machine-facing **Gemma 4 E4B** model host (FastAPI). Runs the small LLM tasks (event extraction, attendance estimates, message rendering) and the tool-using **analytics agent** (SQL over `s10_analysis`). | CUDA GPU | `agent-service/.venv` |

Each component has its own README with full detail:
[pipeline](pipeline/README.md) · [web-app](web-app/README.md) · [agent-service](agent-service/README.md).

## Installation

### Prerequisites

- **Python 3.12** (3.12.10 used in development).
- A **CUDA GPU** for `agent-service` only (Gemma 4 E4B loads in ~5–7 GB VRAM in 4-bit).
  The pipeline and web-app are CPU-only. The dashboard runs without the agent-service —
  only the AI chat needs it.
- **API keys** for the live steps (see [Data & model references](#data--model-references)):
  a **Tavily** key (event web search), a **Google Geocoding** key (event coordinates), and
  a shared **bearer key** (`GOTOAI_AGENT_API_KEY`) so the web-app/pipeline can call the
  agent-service `/v1` API.

Each component keeps its **own** virtualenv — they are intentionally not shared (the
GPU/torch stack in agent-service is isolated from the CPU pipeline/web-app).

### 1. Pipeline (CPU)

```bash
cd pipeline
python3.12 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
```

### 2. Web-app (CPU)

```bash
cd web-app
python3.12 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
cp .env.example .env          # set AGENT_SERVICE_URL + GOTOAI_AGENT_API_KEY
python -m app.geo             # build prefecture-outline GeoJSON cache (one-time)
```

### 3. Agent-service (CUDA GPU)

See [agent-service/INSTALL.md](agent-service/INSTALL.md) for the full, ordered GPU bring-up.
In brief:

```bash
cd agent-service
python3.12 -m venv .venv && source .venv/bin/activate && pip install -U pip
pip install torch torchvision        # CUDA build — see INSTALL.md if cuda.is_available() is False
pip install -r requirements.txt
cp .env.example .env                  # set HF_HOME (shared cache), MODEL_ID, GOTOAI_AGENT_API_KEY
python tests/smoke_test.py           # model loads + one reply + peak VRAM
```

## Running the components

### Pipeline — build the demo data

Run `make` targets from `pipeline/` (`cd pipeline && make help` lists them). Stages are
ordered; each reads the previous stage's outputs under `DATA/`:

```bash
cd pipeline && source .venv/bin/activate
make base-data      # 1. download open gov data (e-Stat census + JMA weather) — long step
make synthetics     # 2. synthesize stores, competitors, homes, events, sales; match stations
make modeling       # 3. build DFM features + train/tune/evaluate the LightGBM model
make prediction     # 4. score next-week sales + SHAP-explain each prediction
make diagnosis      # 5. back-test, residuals, and weather error-slope diagnosis
make search         # 6. LIVE: JMA weather forecast + Tavily event web search
make calibration    # 7. calibrate predictions for weather + events (needs agent-service up)
make analysis       # 8. build the DuckDB-ready s10_analysis layer (dims/facts/marts + SCHEMA.md)
```

Notes:
- `make base-data` downloads ~3 years of JMA weather history and is the long step (~minutes).
- `make search` is **live and spends Tavily credits** — preview `search-events` with `--dry-run`.
- `make calibration` needs the **agent-service running** (event extract + attendance) and a
  **Google Geocoding** key; the final merge is deterministic.

### Agent-service — Gemma model host (GPU)

```bash
cd agent-service && source .venv/bin/activate
make serve                            # or: python -m agent.api  (single worker; model is a VRAM singleton)
```

Serves (bearer-auth on `/v1` when a key is set; interactive docs at `/docs`):
`GET /healthz`, `GET /readyz`, and `POST /v1/{extract-events, estimate-attendance,
present-messages, validate-message, chat, chat/stream}`. It also ships a CLI
(`python -m agent.cli …`) and an interactive analytics REPL (`python -m agent.chatbot`).

### Web-app — area-manager dashboard (CPU)

```bash
cd web-app && source .venv/bin/activate
make serve                            # binds 0.0.0.0:8080 by default (LAN-reachable)
```

Open `http://<host>:8080` (find the host IP with `hostname -I`; set `WEB_HOST=127.0.0.1`
to restrict to localhost). The dashboard works standalone; start the agent-service and
point `AGENT_SERVICE_URL` at it to enable the AI chat.

## Data & model references

The demo is built entirely from **open data** plus a small set of live APIs and one local
model. Each is used under its own terms of use / license — review them before redistributing
any downloaded artifacts.

| Source | Type | Used for |
|---|---|---|
| **政府統計の総合窓口 (e-Stat)** | Open gov statistics (2020 Census 小地域集計) | Prefecture **population** CSVs and **boundary shapefiles** — the population-weighted basis for placing synthetic stores, competitors, homes, and events. |
| **国土数値情報 (MLIT National Land Numerical Information)** | Open geographic reference data | Regional / boundary geographic reference underpinning store and POI placement. |
| **気象庁 (JMA — Japan Meteorological Agency)** | Open weather data & forecast API | AMeDAS station master, ~3 years of **daily weather history** (temperature/rain drivers of sales), and the **live weather forecast** used in calibration. |
| **Tavily Search API** | Live web-search API (key required) | Web-searching near-future local **events** (祭り・花火大会・イベント・コンサート) around each store. |
| **Google Geocoding API** | Geocoding API (key required) | Turning extracted event venue/location text into **coordinates** for map-matching to stores. |
| **Gemma 4 E4B** (`google/gemma-4-E4B-it`) | Local instruction-tuned LLM (4-bit, on GPU) | The analytic AI agents: event **extraction**, **attendance** estimation, **message** rendering, and the SQL tool-using **analytics chat**. |

## Repository layout

```
ichimaru-ramen-delivery-demo/
  pipeline/         # ML pipeline: skills/, config/, Makefile, requirements.txt
  agent-service/    # Gemma model host: agent/ (api, cli, analyst, chatbot, llm, tools), tests/
  web-app/          # dashboard: app/ (FastAPI + HTMX), demo/, tests/
  DATA/             # pipeline outputs, s01_raw … s10_analysis (git-ignored)
  docs/             # pipeline/ and analysis/ design docs (the build contracts)
  CLAUDE.md / AGENTS.md   # agent instructions (mirror copies)
```

`DATA/` is a regenerated build artifact (git-ignored) — reproduce it with the pipeline
`make` steps above; it is never hand-edited.

## License

This repository's own source code is released under the **[MIT License](LICENSE)**
(© 2026 GotoAI Inc.).

The MIT license covers **this project's code only**. Bundled third-party libraries, the
downloaded **Gemma** model, and the open government data / APIs the pipeline consumes each
remain under their **own** licenses and terms of use — see
**[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)**. None of that third-party model or data
is redistributed in this repository; it is downloaded at run time.
