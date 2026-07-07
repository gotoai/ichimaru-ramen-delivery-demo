"""Async client for agent-service (the Gemma model host).

The web-app owns no model; it proxies chat to agent-service over HTTP. The primary path
is the SSE stream (``/v1/chat/stream``) so the browser sees tokens as they generate. If
agent-service is unreachable or errors, the stream yields a single friendly notice rather
than raising — the dashboard stays usable without the model.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from . import config

# agent-service can be slow to first token (model warm-up); give the stream room.
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 300.0


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if config.AGENT_API_KEY:
        h["Authorization"] = f"Bearer {config.AGENT_API_KEY}"
    return h


async def chat(message: str, *, context: str | None = None, history: list[dict] | None = None,
               include_steps: bool = True, max_new_tokens: int = 1024) -> dict:
    """Call agent-service's agentic /v1/chat (non-streaming) and return its result dict
    {message, tool_calls, steps}. The agent runs a multi-step tool loop (querying the
    analytics layer), so this can take a while — hence a long read timeout. On any
    transport/HTTP error, returns a friendly notice as the message.
    """
    payload = {"message": message, "context": context, "history": history or [],
               "include_steps": include_steps, "max_new_tokens": max_new_tokens}
    url = f"{config.AGENT_SERVICE_URL}/v1/chat"
    timeout = httpx.Timeout(600.0, connect=_CONNECT_TIMEOUT)  # agentic loop can be slow
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=_headers(), json=payload)
            if resp.status_code != 200:
                body = resp.text[:200]
                return {"message": _notice(f"AIサービスからエラー応答 ({resp.status_code})", body),
                        "tool_calls": 0, "steps": []}
            return resp.json()
    except httpx.HTTPError as exc:
        return {"message": _notice("AIサービスに接続できませんでした", str(exc)),
                "tool_calls": 0, "steps": []}


async def validate_message(message: str) -> bool:
    """Ask agent-service whether `message` is a meaningful question/request.

    Fail-open: returns True on any transport/HTTP error, so an unavailable validator never
    blocks a legitimate question (the chat call itself will surface a connection problem).
    """
    url = f"{config.AGENT_SERVICE_URL}/v1/validate-message"
    timeout = httpx.Timeout(30.0, connect=_CONNECT_TIMEOUT)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=_headers(), json={"message": message})
            if resp.status_code != 200:
                return True
            return bool(resp.json().get("valid", True))
    except (httpx.HTTPError, ValueError):
        return True


async def stream_chat(
    message: str,
    *,
    context: str | None = None,
    history: list[dict] | None = None,
    max_new_tokens: int = 1024,
) -> AsyncIterator[str]:
    """Yield reply text deltas from agent-service's SSE chat endpoint.

    Yields plain text chunks (already-decoded deltas). On transport/HTTP errors, yields a
    single Japanese notice so the caller can render it as the assistant's reply.
    """
    payload = {"message": message, "context": context,
               "history": history or [], "max_new_tokens": max_new_tokens}
    url = f"{config.AGENT_SERVICE_URL}/v1/chat/stream"
    timeout = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=_headers(), json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:200]
                    yield _notice(f"AIサービスからエラー応答 ({resp.status_code})", body)
                    return
                async for chunk in _iter_sse(resp):
                    yield chunk
    except httpx.HTTPError as exc:
        yield _notice("AIサービスに接続できませんでした", str(exc))


async def _iter_sse(resp: httpx.Response) -> AsyncIterator[str]:
    """Parse an SSE stream, yielding the text of each `data: {"delta": ...}` line and
    stopping on the `done`/`error` events."""
    event = "message"
    async for line in resp.aiter_lines():
        if line == "":
            event = "message"  # blank line ends an event block
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if event == "done":
            return
        if event == "error":
            try:
                msg = json.loads(data).get("error", "")
            except json.JSONDecodeError:
                msg = data
            yield _notice("AI生成中にエラーが発生しました", msg)
            return
        try:
            delta = json.loads(data).get("delta", "")
        except json.JSONDecodeError:
            continue
        if delta:
            yield delta


def _notice(title: str, detail: str = "") -> str:
    detail = f"（{detail}）" if detail else ""
    return f"⚠️ {title}{detail}"
