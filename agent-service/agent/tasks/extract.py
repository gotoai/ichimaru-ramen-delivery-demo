"""Task 1 — extract clean, structured events from raw search text.

Input: search results for one location (title/content/url). Output: structured events
with dates, location, type. Pure extraction only (no relevance filtering, no crowd
size — those are separate tasks).
"""
from __future__ import annotations

from ._jsonio import parse_json_array

PROMPT_VERSION = "extract/v1"

EVENT_TYPES = ["concert", "festival", "fireworks", "market", "sports", "exhibition", "other"]

# Tavily "advanced" results carry ~2k chars each; feeding all of them for 8 items is a
# ~16k-token prompt that OOMs a 16GB GPU at prefill. Truncate — the event signal (name,
# date, venue) is near the top — to keep the prompt small.
MAX_CONTENT_CHARS = 600
MAX_TITLE_CHARS = 200


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"

_SYSTEM = (
    "あなたは日本のイベント情報を抽出するアシスタントです。"
    "与えられた検索結果から、実際に開催される具体的なイベントのみを抽出します。"
    "検索結果に根拠がない情報は決して創作しないでください。"
    "出力は指定されたJSON配列のみとし、前後に説明文を付けないでください。"
)

_INSTRUCTIONS = (
    "次の検索結果から、開催イベントを抽出してJSON配列で返してください。\n"
    "各要素は以下のキーを持ちます:\n"
    '  "event_name": イベント名 (string)\n'
    f'  "event_type": {"|".join(EVENT_TYPES)} のいずれか\n'
    '  "start_date": 開始日 YYYY-MM-DD (不明なら "")\n'
    '  "end_date":   終了日 YYYY-MM-DD (単日なら start_date と同じ, 不明なら "")\n'
    '  "location":   開催地の市区町村など (string)\n'
    '  "venue":      会場名 (不明なら "")\n'
    '  "source_url": 根拠となった検索結果のURL\n'
    '  "confidence": 0.0〜1.0 の確信度 (number)\n'
    "根拠が弱い場合は confidence を下げてください。該当が無ければ空配列 [] を返します。"
)


def build_messages(items: list[dict], location: str | None = None,
                   max_content_chars: int = MAX_CONTENT_CHARS) -> list[dict]:
    """Build the chat messages for extraction. `items`: [{title, content, url, published_date?}].

    `content` is truncated to `max_content_chars` to keep the prompt small enough to fit
    in VRAM (see MAX_CONTENT_CHARS).
    """
    lines = []
    if location:
        lines.append(f"対象地域: {location}\n")
    lines.append(_INSTRUCTIONS)
    lines.append("\n--- 検索結果 ---")
    for i, it in enumerate(items, 1):
        lines.append(
            f"[{i}] title: {_clip(it.get('title', ''), MAX_TITLE_CHARS)}\n"
            f"    url: {it.get('url', '')}\n"
            f"    published: {it.get('published_date', '')}\n"
            f"    content: {_clip(it.get('content', ''), max_content_chars)}"
        )
    user = "\n".join(lines)
    return [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM}]},
        {"role": "user", "content": [{"type": "text", "text": user}]},
    ]


def parse_events(text: str) -> list[dict]:
    """Parse the model reply into a list of event dicts (best-effort)."""
    events = []
    for ev in parse_json_array(text):
        if not isinstance(ev, dict) or not ev.get("event_name"):
            continue
        etype = str(ev.get("event_type", "other"))
        events.append({
            "event_name": str(ev.get("event_name", "")).strip(),
            "event_type": etype if etype in EVENT_TYPES else "other",
            "start_date": str(ev.get("start_date", "")).strip(),
            "end_date": str(ev.get("end_date", "")).strip(),
            "location": str(ev.get("location", "")).strip(),
            "venue": str(ev.get("venue", "")).strip(),
            "source_url": str(ev.get("source_url", "")).strip(),
            "confidence": ev.get("confidence", ""),
        })
    return events


def extract_events(items: list[dict], location: str | None = None, llm=None,
                   max_new_tokens: int = 2048,
                   max_content_chars: int = MAX_CONTENT_CHARS) -> list[dict]:
    """Run extraction: build prompt -> generate (greedy) -> parse."""
    from ..llm import get_llm  # lazy: keeps this module torch-free until used

    llm = llm or get_llm()
    reply = llm.generate(build_messages(items, location, max_content_chars),
                         do_sample=False, max_new_tokens=max_new_tokens)
    return parse_events(reply)
