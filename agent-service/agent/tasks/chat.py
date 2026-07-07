"""Task 6 — grounded conversational chat for the area-manager dashboard.

The web-app injects the currently-selected store's structured calibration data as
``context`` and forwards the manager's question. As with present-messages, faithfulness
matters: the assistant explains the numbers it is given and must not invent figures.

Kept torch-free (``build_messages`` only) so it stays unit-testable offline; the LLM call
lives in the API layer, which can stream the reply.
"""
from __future__ import annotations

PROMPT_VERSION = "chat/v1"

_SYSTEM = (
    "あなたはイチマルラーメンのエリアマネージャーを支援するアシスタントです。"
    "販売予測と補正（天候・イベント）の仕組みや数値を分かりやすく説明します。"
    "回答は日本語で簡潔に。与えられたデータに存在する数値・事実のみを用い、"
    "データに無い数値を創作しないでください。発注量は最終的にマネージャーが判断します。"
    "数式はLaTeX（$記号など）を使わず、プレーンテキストで書いてください。"
)


def build_messages(message: str, *, context: str | None = None,
                   history: list[dict] | None = None) -> list[dict]:
    """Build chat messages: system + optional prior turns + the current question.

    `context` is a JSON/TSV string of the selected store's calibration data (optional).
    `history` is a list of ``{"role": "user"|"assistant", "text": "..."}`` prior turns.
    """
    messages: list[dict] = [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM}]},
    ]
    for turn in history or []:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        text = turn.get("text", "")
        if text:
            messages.append({"role": role, "content": [{"type": "text", "text": text}]})

    parts = []
    if context:
        parts.append("--- 参考データ ---")
        parts.append(context)
        parts.append("--- 質問 ---")
    parts.append(message)
    messages.append({"role": "user", "content": [{"type": "text", "text": "\n".join(parts)}]})
    return messages
