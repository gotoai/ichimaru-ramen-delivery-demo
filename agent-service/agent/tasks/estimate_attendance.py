"""Task 2 — estimate how many people attend each event.

Input: structured events (extract-events's output) + optional context. Output: a crowd-size
estimate as a RANGE (low/point/high) with confidence and rationale — crowd size is
genuinely uncertain, so avoid false precision.
"""
from __future__ import annotations

from ._jsonio import parse_json_array

PROMPT_VERSION = "estimate-attendance/v1"

_SYSTEM = (
    "あなたは日本のイベントの来場者数を推定するアナリストです。"
    "イベントの種類・会場・規模・時期から現実的な来場者数を見積もります。"
    "不確実性を考慮し、点推定だけでなく下限・上限の幅も示します。"
    "出力は指定されたJSON配列のみとし、説明文を付けないでください。"
)


def build_messages(events: list[dict], context: dict | None = None) -> list[dict]:
    """Build the chat messages for attendance estimation."""
    ctx = ""
    if context:
        ctx = "参考情報: " + ", ".join(f"{k}={v}" for k, v in context.items()) + "\n"
    listing = "\n".join(
        f"[{i}] {e.get('event_name','')} "
        f"(type={e.get('event_type','')}, date={e.get('start_date','')}, "
        f"venue={e.get('venue','')}, location={e.get('location','')})"
        for i, e in enumerate(events, 1)
    )
    user = (
        f"{ctx}次の各イベントについて、来場者数を推定してJSON配列で返してください。\n"
        "各要素のキー:\n"
        '  "event_name": 対象イベント名 (入力と一致)\n'
        '  "expected_attendance": {"point": 整数, "low": 整数, "high": 整数}\n'
        '  "confidence": 0.0〜1.0\n'
        '  "rationale": 推定の根拠 (簡潔に)\n'
        f"\n--- イベント一覧 ---\n{listing}"
    )
    return [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM}]},
        {"role": "user", "content": [{"type": "text", "text": user}]},
    ]


def parse_estimates(text: str) -> list[dict]:
    """Parse the model reply into attendance estimates (best-effort)."""
    out = []
    for e in parse_json_array(text):
        if not isinstance(e, dict) or not e.get("event_name"):
            continue
        att = e.get("expected_attendance") or {}
        if not isinstance(att, dict):
            att = {}
        out.append({
            "event_name": str(e.get("event_name", "")).strip(),
            "expected_attendance": {
                "point": att.get("point", ""),
                "low": att.get("low", ""),
                "high": att.get("high", ""),
            },
            "confidence": e.get("confidence", ""),
            "rationale": str(e.get("rationale", "")).strip(),
        })
    return out


def estimate_attendance(events: list[dict], context: dict | None = None,
                        llm=None, max_new_tokens: int = 2048) -> list[dict]:
    # 1024 truncates the JSON for ~10 events (each carries a rationale) -> parse fails -> [].
    if llm is None:
        from ..llm import get_llm  # lazy: import torch only when we must load the model

        llm = get_llm()
    reply = llm.generate(build_messages(events, context),
                         do_sample=False, max_new_tokens=max_new_tokens)
    return parse_estimates(reply)
