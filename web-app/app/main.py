"""FastAPI app for the area-manager dashboard.

Three kinds of routes:
  * Pages (HTML)        — the dashboard shell.
  * JSON API (/api/...) — feeds the JS islands (Leaflet map, ECharts chart).
  * HTMX fragments (/ui/...) — server-rendered HTML swapped into the page (forecast
    table, per-day breakdown), plus the chat SSE bridge to agent-service.

The app owns no model. Chat is proxied to agent-service (see agent_client). All numbers
come from the pipeline's DATA/ outputs via the data layer — the AI only explains them.
"""
from __future__ import annotations

import json
import re

from fastapi import FastAPI, Form, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import agent_client, config, data, geo

app = FastAPI(title="ichimaru web-app", version="0.1.0")
# Compress the prefecture GeoJSON (~1MB) and other large JSON responses.
app.add_middleware(GZipMiddleware, minimum_size=1024)

templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "app" / "static")), name="static")


def _d() -> data.Data:
    return data.get_data()


# --------------------------------------------------------------------------- page
@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    d = _d()
    return templates.TemplateResponse(request, "index.html", {
        "stores": d.stores(),
        "forecast_start": data.tomorrow_jst(),
        "ui_language": config.UI_LANGUAGE,
    })


# --------------------------------------------------------------------------- JSON API
@app.get("/api/stores")
async def api_stores() -> JSONResponse:
    return JSONResponse(_d().stores())


@app.get("/api/events")
async def api_events() -> JSONResponse:
    return JSONResponse(_d().events())


@app.get("/api/geojson/prefectures")
async def api_geojson() -> JSONResponse:
    return JSONResponse(geo.prefecture_geojson())


@app.get("/api/store/{store_name}/series")
async def api_series(store_name: str) -> JSONResponse:
    d = _d()
    if not d.has_store(store_name):
        return JSONResponse({"error": "unknown store"}, status_code=404)
    return JSONResponse({"store_name": store_name, "rows": d.forecast(store_name)})


@app.get("/api/store/{store_name}/shap")
async def api_shap(store_name: str, target: str | None = None) -> JSONResponse:
    """SHAP waterfall decomposition for the store's forecast (default: tomorrow)."""
    d = _d()
    if not d.has_store(store_name):
        return JSONResponse({"error": "unknown store"}, status_code=404)
    wf = d.shap_waterfall(store_name, target)
    if wf is None:
        return JSONResponse({"error": "no shap data"}, status_code=404)
    return JSONResponse(wf)


# --------------------------------------------------------------------------- HTMX fragments
@app.get("/ui/store/{store_name}/forecast", response_class=HTMLResponse)
async def ui_forecast(request: Request, store_name: str) -> HTMLResponse:
    d = _d()
    if not d.has_store(store_name):
        return HTMLResponse("<p class='error'>不明な店舗です。</p>", status_code=404)
    return templates.TemplateResponse(request, "_forecast_table.html", {
        "store_name": store_name, "rows": d.forecast(store_name),
    })


@app.get("/ui/store/{store_name}/breakdown", response_class=HTMLResponse)
async def ui_breakdown(request: Request, store_name: str, target: str) -> HTMLResponse:
    d = _d()
    bd = d.breakdown(store_name, target)
    if bd is None:
        return HTMLResponse("<p class='error'>内訳データが見つかりません。</p>", status_code=404)
    # Waterfall segments (widths computed in the template from these values).
    return templates.TemplateResponse(request, "_breakdown.html", {
        "bd": bd, "labels": data.CHAIN_LABELS,
    })


# --------------------------------------------------------------------------- chat (SSE)
# A "word" character: ASCII alnum, hiragana, katakana (full/half width), or CJK.
_WORD_CHAR = re.compile(r"[0-9A-Za-z぀-ヿ㐀-鿿０-ﾟ]")
_MEANINGLESS_REPLY = "質問の意味はわかりません。恐れ入りますが、もう一度質問を入力してください。"


def _precheck_reply(message: str) -> str | None:
    """Cheap guard so obviously meaningless input never reaches the model. Returns a
    canned Japanese notice when the message looks meaningless (empty, only symbols, or a
    single character repeated like 'AAAA'/'ababab'), else None."""
    compact = re.sub(r"\s+", "", message)
    uniq = len(set(compact.lower()))
    meaningless = (
        len(compact) < 2                              # too short / empty
        or not _WORD_CHAR.search(compact)             # only punctuation / symbols
        or (uniq == 1 and len(compact) >= 3)          # 'AAA', '。。。。'
        or (uniq <= 2 and len(compact) >= 6)          # 'AAAAAA', 'abababab'
    )
    return _MEANINGLESS_REPLY if meaningless else None


@app.post("/ui/chat")
async def ui_chat(message: str = Form(...), store: str | None = Form(None),
                  history: str | None = Form(None)) -> JSONResponse:
    """Agentic analytics chat (non-streaming). A fast heuristic rejects trivial junk
    without calling the model; otherwise the agent (agent-service /v1/chat) queries the
    analytics layer and returns {message, tool_calls, steps}. `history` (JSON list of
    {role, text} from the browser) gives the agent memory of the session; the selected
    store is passed as a light reference note so the agent can scope its queries."""
    canned = _precheck_reply(message)
    if canned is not None:
        return JSONResponse({"message": canned, "tool_calls": 0, "steps": []})
    hist = None
    if history:
        try:
            parsed = json.loads(history)
            if isinstance(parsed, list):
                hist = [t for t in parsed if isinstance(t, dict) and t.get("text")]
        except json.JSONDecodeError:
            hist = None
    d = _d()
    context = f"現在選択中の店舗: {store}" if store and d.has_store(store) else None
    result = await agent_client.chat(message, context=context, history=hist, include_steps=True)
    return JSONResponse(result)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(config.ASSETS_DIR / "favicon.ico")


_SHAP_EXPLAIN_INSTRUCTION = (
    "次のSHAP要因分解データに基づき、予測売上への寄与が大きい順に、上位の各要因が売上を"
    "どれだけ押し上げた（プラス）／押し下げた（マイナス）のかを、エリアマネージャー向けに"
    "専門用語を避けて分かりやすく説明してください。各要因は「要因名（寄与 ±N杯）」に続けて"
    "1〜2文で。最後に全体を一言でまとめてください。データに無い数値は書かないでください。"
)


@app.get("/ui/store/{store_name}/shap/explain")
async def ui_shap_explain(store_name: str, target: str | None = None) -> StreamingResponse:
    """Stream an AI, plain-language explanation of the top SHAP drivers (grounded in the
    SHAP payload). Used by the 予測数量の分解 button alongside the waterfall chart."""
    d = _d()
    payload = d.shap_explanation(store_name, target) if d.has_store(store_name) else None
    context = json.dumps(payload, ensure_ascii=False) if payload else None

    async def _sse():
        if context is None:
            note = "SHAPデータが見つかりませんでした。"
            yield f"data: {json.dumps({'delta': note}, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
            return
        async for delta in agent_client.stream_chat(_SHAP_EXPLAIN_INSTRUCTION, context=context):
            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, workers=1)


if __name__ == "__main__":
    main()
