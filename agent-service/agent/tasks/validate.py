"""Task 7 — pre-check: is the user's message a meaningful question/request?

A cheap heuristic on the web-app side catches trivial junk (empty, repeated chars), but
high-entropy random strings (e.g. 'jRFvakeDcvB5cF3dV8H_BF6GARLPxdPDC') look word-like and
slip through. This task lets the model make the call before the (expensive) real answer is
generated. It is deliberately a one-word classifier: deterministic (greedy), tiny output.

Kept torch-free (``build_messages`` / ``parse`` only); the LLM call lives in the API layer.
"""
from __future__ import annotations

PROMPT_VERSION = "validate-message/v1"

_SYSTEM = (
    "あなたは入力チェック係です。ユーザーの入力が、人間が書いた意味のある"
    "自然言語の質問または依頼かどうかだけを判定します。"
)


def build_messages(message: str) -> list[dict]:
    instr = (
        "次の入力を判定してください。"
        "日本語または英語で書かれた、意味の通る質問・依頼であれば VALID。"
        "ランダムな文字列・記号の羅列・意味不明な入力であれば INVALID。"
        "VALID か INVALID の一語だけを出力してください。\n\n"
        f"入力:\n{message}"
    )
    return [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM}]},
        {"role": "user", "content": [{"type": "text", "text": instr}]},
    ]


def parse(text: str) -> bool:
    """True when the model judged the message VALID. Fail open (True) if the output is
    unexpected, so the pre-check never wrongly blocks a real question."""
    t = (text or "").strip().upper()
    if "INVALID" in t:            # check INVALID first — it contains 'VALID'
        return False
    if "VALID" in t:
        return True
    return True
