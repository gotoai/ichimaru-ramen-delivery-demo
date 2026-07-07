# web-app

The **area-manager dashboard** for Ichimaru. A FastAPI + Jinja2 + **HTMX** app that shows
the pipeline's **calibrated sales** (`DATA/s09_calibration/`) so area managers can decide
order quantities, with an **interactive AI chat** that explains the forecast and
calibration.

It is **human-facing** — the counterpart to `agent-service` (the machine-facing Gemma
model host). This app owns **no model**: it reads `DATA/` directly and **proxies chat** to
`agent-service`.

```
Browser (map + chart + chat)
   │  HTTP / SSE
web-app (this)  ── reads DATA/s06,s09 ── serves tables, charts, breakdowns
   │  proxies chat (SSE)
agent-service (Gemma on GPU)  ── /v1/chat/stream, /v1/present-messages
```

## What it shows

- **Map** (Leaflet + OSM): every store, colored by mean calibrated demand; event markers.
- **Chart** (ECharts): the 7-day series — predicted vs weather-calibrated vs calibrated.
- **Forecast table**: per-day `predicted → weather-calibrated → +event = calibrated`, with
  a rounded **order-quantity hint** (the decision is still the manager's).
- **Per-day breakdown**: a waterfall plus the *why* — weather bias factors, nearby events,
  and the top **SHAP** drivers of the underlying forecast (all from the pipeline; the AI
  never computes these).
- **Chat**: ask about trends or the calibration mechanism; answers stream token-by-token
  from the local Gemma model and are grounded in the selected store's data.

## Setup (CPU box — no GPU/model here)

```bash
cd web-app
python3.12 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
cp .env.example .env            # set AGENT_SERVICE_URL + GOTOAI_AGENT_API_KEY (match agent-service)
python -m app.geo               # build the prefecture-outline GeoJSON cache (one-time, ~15s)
make serve                      # binds 0.0.0.0:8080
```

By default the server binds `0.0.0.0`, so other machines on the same LAN can open it at
`http://<this-host-LAN-IP>:8080` (e.g. `http://192.168.x.y:8080`). Find the host IP with
`hostname -I`. Set `WEB_HOST=127.0.0.1` in `.env` to restrict to localhost. If a LAN
client can't connect, check the host firewall allows inbound TCP on `WEB_PORT`.

The dashboard (map, chart, tables, breakdown) works **without** `agent-service`; only the
chat needs it running. Start `agent-service` on its GPU box and point `AGENT_SERVICE_URL`
at it to enable chat.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /` | dashboard shell |
| `GET /api/stores?ref=` | stores + coords + mean calibrated (map) |
| `GET /api/events` | events + geo + attendance (map) |
| `GET /api/geojson/prefectures` | dissolved prefecture outlines (cached) |
| `GET /api/store/{name}/series?ref=` | 7-day series (chart) |
| `GET /ui/store/{name}/forecast?ref=` | HTMX: forecast table |
| `GET /ui/store/{name}/breakdown?target=&ref=` | HTMX: per-day waterfall + factors + SHAP |
| `GET /ui/chat/stream?message=&store=&ref=` | SSE: chat, proxied to agent-service |

## Config (`.env`)

| Key | Meaning |
|---|---|
| `DATA_DIR` | pipeline `DATA/` tree (default: `../DATA`) |
| `AGENT_SERVICE_URL` | agent-service base URL (default `http://127.0.0.1:8000`) |
| `GOTOAI_AGENT_API_KEY` | bearer key for agent-service `/v1` (must match it) |
| `WEB_HOST` / `WEB_PORT` | this app's bind address (default `127.0.0.1:8080`) |
| `UI_LANGUAGE` | `ja` (default) |

## Layout

```
web-app/
  app/
    config.py        # paths + upstream settings (loads .env)
    data.py          # in-memory view of the calibration outputs (loaded once)
    geo.py           # shapefiles -> dissolved prefecture GeoJSON (cached)
    agent_client.py  # async SSE client for agent-service chat
    main.py          # FastAPI: pages + /api + /ui fragments + chat bridge
    templates/       # index.html + HTMX fragments
    static/          # app.js (map/chart/chat glue), styles.css, vendor/ (leaflet,echarts,htmx)
  data/geojson/      # generated cache (git-ignored)
  tests/
```
