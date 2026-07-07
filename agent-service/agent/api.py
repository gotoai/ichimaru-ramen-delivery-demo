"""Live web API for agent-service (FastAPI).

The task modules already keep a torch-free ``build_messages`` / ``parse_*`` pair separate
from the LLM call, so this file is a thin wrapper: it validates the request, calls the
task, and returns the structured result. LLM tasks exposed today —
``POST /v1/extract-events`` and ``POST /v1/estimate-attendance`` — plus
liveness/readiness probes. The remaining tasks follow the same shape.

Operational model (why this file looks the way it does):
  * ONE model in ONE process. The Gemma model is a process-wide singleton in VRAM
    (agent.llm.get_llm). Run a SINGLE uvicorn worker — never --workers N, or you load
    N copies of the model. Scale by GPU/replica instead.
  * Generation is blocking and GPU-bound. Each request runs generate() in a worker
    thread (so the event loop stays responsive) and holds a global lock so only ONE
    generation runs at a time; extra requests queue rather than thrash VRAM / OOM.

Run it (single worker):
    python -m agent.api                         # host/port from .env (API_HOST/API_PORT)
    uvicorn agent.api:app --host 127.0.0.1 --port 8000   # equivalent, explicit

Call it:
    curl -sS -X POST http://127.0.0.1:8000/v1/extract-events \
      -H "Authorization: Bearer $GOTOAI_AGENT_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"location":"東京都世田谷区","items":[{"title":"...","content":"...","url":"..."}]}'
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import analyst, config
from .tasks import chat, estimate_attendance, extract_events, present_messages, validate

# Only one generation at a time — the GPU can serve a single generate() efficiently, and
# concurrent calls risk an OOM. Requests beyond the first queue on this lock.
_gpu_lock = asyncio.Lock()


def _load_llm():
    """Return the process-wide LLM singleton (loads the model on first call).

    Imported lazily so importing ``agent.api`` does NOT import torch — the app and its
    tests stay torch-free until a real generation is served (tests stub this out).
    """
    from .llm import get_llm
    return get_llm()


def _model_ready() -> bool:
    """True once the model is loaded. Guarded/torch-free: returns False if the LLM layer
    isn't importable (torch absent) or the model simply hasn't loaded yet."""
    try:
        from . import llm as _llm
    except Exception:
        return False
    return _llm._LLM is not None and _llm._LLM.model is not None


# --------------------------------------------------------------------------- schemas
class SearchItem(BaseModel):
    """One search result fed to the extractor (mirrors what build_messages consumes)."""
    title: str = ""
    content: str = ""
    url: str = ""
    published_date: str = ""


class ExtractEventsRequest(BaseModel):
    items: list[SearchItem] = Field(..., min_length=1,
                                    description="Search results to extract events from.")
    location: str | None = Field(None, description="Target area, e.g. 東京都世田谷区 (optional).")
    max_new_tokens: int = Field(config.MAX_NEW_TOKENS, ge=1, le=8192)
    max_content_chars: int = Field(extract_events.MAX_CONTENT_CHARS, ge=1, le=8192,
                                   description="Per-item content truncation (VRAM safety).")


class Event(BaseModel):
    event_name: str
    event_type: str
    start_date: str = ""
    end_date: str = ""
    location: str = ""
    venue: str = ""
    source_url: str = ""
    # parse_events passes confidence through untyped (a number, or "" when absent).
    confidence: float | str | None = None


class ExtractEventsResponse(BaseModel):
    events: list[Event]
    location: str | None
    item_count: int
    event_count: int
    prompt_version: str
    model_id: str


class EstimateEventIn(BaseModel):
    """One event to estimate (mirrors what estimate_attendance.build_messages consumes —
    typically an extract-events output event; extra fields are ignored)."""
    event_name: str = Field(..., min_length=1)
    event_type: str = ""
    start_date: str = ""
    venue: str = ""
    location: str = ""


class EstimateAttendanceRequest(BaseModel):
    events: list[EstimateEventIn] = Field(..., min_length=1,
                                          description="Events to estimate attendance for.")
    context: dict[str, str] | None = Field(
        None, description="Optional key=value hints prepended to the prompt (参考情報).")
    max_new_tokens: int = Field(config.MAX_NEW_TOKENS, ge=1, le=8192)


class AttendanceRange(BaseModel):
    # parse_estimates passes these through untyped (an integer, or "" when absent).
    point: int | str | None = None
    low: int | str | None = None
    high: int | str | None = None


class AttendanceEstimate(BaseModel):
    event_name: str
    expected_attendance: AttendanceRange
    confidence: float | str | None = None
    rationale: str = ""


class EstimateAttendanceResponse(BaseModel):
    estimates: list[AttendanceEstimate]
    event_count: int
    estimate_count: int
    prompt_version: str
    model_id: str


class PresentMessagesRequest(BaseModel):
    """Structured data (JSON/TSV string) -> faithful natural-language prose."""
    data: str = Field(..., min_length=1, description="JSON or TSV to describe.")
    fmt: str = Field("json", description="'json' or 'tsv' (labels the data block).")
    language: str = "ja"
    audience: str | None = None
    style: str = Field("brief", description="brief | detailed | bullet.")
    instructions: str | None = None
    max_new_tokens: int = Field(1024, ge=1, le=8192)


class PresentMessagesResponse(BaseModel):
    message: str
    prompt_version: str
    model_id: str


class ChatTurn(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'.")
    text: str = ""


class ChatRequest(BaseModel):
    """A chat turn. /v1/chat runs the agentic analyst (queries the analytics layer); the
    optional `context` (e.g. the selected store's data) is passed as a reference note."""
    message: str = Field(..., min_length=1)
    context: str | None = Field(None, description="Optional reference data (JSON/TSV) for the turn.")
    history: list[ChatTurn] | None = Field(None, description="Prior turns (oldest first).")
    max_new_tokens: int = Field(1024, ge=1, le=8192)
    include_steps: bool = Field(False, description="Include the tool-call transcript in the response.")


class ChatStep(BaseModel):
    tool: str
    input: dict
    output: str


class ChatResponse(BaseModel):
    message: str
    prompt_version: str
    model_id: str
    tool_calls: int = 0
    steps: list[ChatStep] | None = None


class ValidateMessageRequest(BaseModel):
    """A message to pre-check before answering (is it a meaningful question/request?)."""
    message: str = Field(..., min_length=1)
    max_new_tokens: int = Field(8, ge=1, le=64)


class ValidateMessageResponse(BaseModel):
    valid: bool
    prompt_version: str
    model_id: str


# --------------------------------------------------------------------------- auth
def require_auth(authorization: str | None = Header(None)) -> None:
    """Enforce `Authorization: Bearer <GOTOAI_AGENT_API_KEY>` when a key is configured."""
    key = config.GOTOAI_AGENT_API_KEY
    if not key:  # auth disabled (dev) — startup already warned
        return
    expected = f"Bearer {key}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


# --------------------------------------------------------------------------- lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.GOTOAI_AGENT_API_KEY:
        print("WARNING: GOTOAI_AGENT_API_KEY is empty — /v1 endpoints are UNAUTHENTICATED.",
              file=sys.stderr, flush=True)
    if config.API_EAGER_LOAD:
        # Load the model up front (in a thread — it's slow and blocking) so the first real
        # request isn't paying the load cost and /readyz reflects reality.
        print("Eager-loading model at startup (API_EAGER_LOAD=1)...", file=sys.stderr, flush=True)
        await anyio.to_thread.run_sync(_load_llm)
    yield


app = FastAPI(title="agent-service", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- probes
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up (does not require the model to be loaded)."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, Any]:
    """Readiness: the model is loaded and can serve generations."""
    ready = _model_ready()
    if not ready:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    return {"status": "ready", "model_id": config.MODEL_ID}


# --------------------------------------------------------------------------- endpoint
@app.post("/v1/extract-events", response_model=ExtractEventsResponse,
          dependencies=[Depends(require_auth)])
async def extract_events_endpoint(req: ExtractEventsRequest) -> ExtractEventsResponse:
    """Search text -> structured events. Runs one greedy generation on the GPU."""
    items = [it.model_dump() for it in req.items]

    def _run() -> list[dict]:
        return extract_events.extract_events(
            items, req.location, llm=_load_llm(),
            max_new_tokens=req.max_new_tokens, max_content_chars=req.max_content_chars,
        )

    async with _gpu_lock:
        try:
            events = await anyio.to_thread.run_sync(_run)
        except Exception as exc:  # OOM, model errors — surface as 503 (retryable)
            raise HTTPException(status_code=503, detail=f"generation failed: {exc}") from exc

    return ExtractEventsResponse(
        events=events, location=req.location, item_count=len(items),
        event_count=len(events), prompt_version=extract_events.PROMPT_VERSION,
        model_id=config.MODEL_ID,
    )


@app.post("/v1/estimate-attendance", response_model=EstimateAttendanceResponse,
          dependencies=[Depends(require_auth)])
async def estimate_attendance_endpoint(req: EstimateAttendanceRequest) -> EstimateAttendanceResponse:
    """Events -> crowd-size estimates (range + confidence). One greedy GPU generation."""
    events = [ev.model_dump() for ev in req.events]

    def _run() -> list[dict]:
        return estimate_attendance.estimate_attendance(
            events, req.context, llm=_load_llm(), max_new_tokens=req.max_new_tokens,
        )

    async with _gpu_lock:
        try:
            estimates = await anyio.to_thread.run_sync(_run)
        except Exception as exc:  # OOM, model errors — surface as 503 (retryable)
            raise HTTPException(status_code=503, detail=f"generation failed: {exc}") from exc

    return EstimateAttendanceResponse(
        estimates=estimates, event_count=len(events), estimate_count=len(estimates),
        prompt_version=estimate_attendance.PROMPT_VERSION, model_id=config.MODEL_ID,
    )


@app.post("/v1/present-messages", response_model=PresentMessagesResponse,
          dependencies=[Depends(require_auth)])
async def present_messages_endpoint(req: PresentMessagesRequest) -> PresentMessagesResponse:
    """Structured data -> faithful prose. One sampled GPU generation."""
    def _run() -> str:
        return present_messages.present(
            req.data, fmt=req.fmt, language=req.language, audience=req.audience,
            style=req.style, instructions=req.instructions, llm=_load_llm(),
            max_new_tokens=req.max_new_tokens,
        )

    async with _gpu_lock:
        try:
            message = await anyio.to_thread.run_sync(_run)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"generation failed: {exc}") from exc

    return PresentMessagesResponse(
        message=message, prompt_version=present_messages.PROMPT_VERSION, model_id=config.MODEL_ID,
    )


@app.post("/v1/chat", response_model=ChatResponse, dependencies=[Depends(require_auth)])
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    """Agentic analytics chat, non-streaming.

    Runs the analyst tool loop (agent.analyst): it queries the s10_analysis DuckDB layer
    via run_sql (sandboxed) and iterates until it produces a final answer. This is NOT
    streaming — a multi-step tool loop has no single token stream. The whole loop runs in
    a worker thread under the process-wide GPU lock (one generation at a time), so tool
    steps and generations for concurrent requests queue rather than thrash the GPU.
    """
    history = [t.model_dump() for t in (req.history or [])]

    def _run() -> dict:
        return analyst.answer(req.message, history=history, context=req.context,
                              llm=_load_llm(), max_new_tokens=req.max_new_tokens)

    async with _gpu_lock:
        try:
            result = await anyio.to_thread.run_sync(_run)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"agent failed: {exc}") from exc

    steps = None
    if req.include_steps:
        steps = [ChatStep(tool=s["tool"], input=s["input"],
                          output=(s["output"] if len(s["output"]) <= 1500 else s["output"][:1500] + "\n[...]"))
                 for s in result["steps"]]
    return ChatResponse(message=result["message"], prompt_version=analyst.PROMPT_VERSION,
                        model_id=config.MODEL_ID, tool_calls=result["tool_calls"], steps=steps)


@app.post("/v1/validate-message", response_model=ValidateMessageResponse,
          dependencies=[Depends(require_auth)])
async def validate_message_endpoint(req: ValidateMessageRequest) -> ValidateMessageResponse:
    """Pre-check: is the message a meaningful question/request? One tiny greedy generation."""
    messages = validate.build_messages(req.message)

    def _run() -> bool:
        raw = _load_llm().generate(messages, do_sample=False, max_new_tokens=req.max_new_tokens)
        return validate.parse(raw)

    async with _gpu_lock:
        try:
            ok = await anyio.to_thread.run_sync(_run)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"generation failed: {exc}") from exc

    return ValidateMessageResponse(valid=ok, prompt_version=validate.PROMPT_VERSION,
                                   model_id=config.MODEL_ID)


@app.post("/v1/chat/stream", dependencies=[Depends(require_auth)])
async def chat_stream_endpoint(req: ChatRequest) -> StreamingResponse:
    """Grounded chat, Server-Sent Events. Streams token deltas as they generate.

    Events: ``data: {"delta": "..."}`` per chunk, a final ``event: done``, or
    ``event: error`` with a message. The GPU lock is held for the stream's duration, so
    concurrent chat requests queue (one generation at a time), consistent with the other
    endpoints.
    """
    history = [t.model_dump() for t in (req.history or [])]
    messages = chat.build_messages(req.message, context=req.context, history=history)

    async def _sse():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        def _worker() -> None:
            try:
                for piece in _load_llm().generate_stream(messages, max_new_tokens=req.max_new_tokens):
                    loop.call_soon_threadsafe(queue.put_nowait, ("delta", piece))
            except Exception as exc:  # noqa: BLE001 — surface to the client as an error event
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", _SENTINEL))

        async with _gpu_lock:
            await anyio.to_thread.run_sync(lambda: None)  # ensure loop is live
            import threading
            threading.Thread(target=_worker, daemon=True).start()
            while True:
                kind, payload = await queue.get()
                if kind == "delta":
                    yield f"data: {json.dumps({'delta': payload}, ensure_ascii=False)}\n\n"
                elif kind == "error":
                    yield f"event: error\ndata: {json.dumps({'error': payload}, ensure_ascii=False)}\n\n"
                    return
                else:  # done
                    yield "event: done\ndata: {}\n\n"
                    return

    return StreamingResponse(_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def main() -> None:
    import uvicorn

    # Single worker on purpose: one model per process (see module docstring).
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, workers=1)


if __name__ == "__main__":
    main()
