"""Task 3 — turn structured data (TSV/JSON) into a natural-language message.

Renders structured rows into prose for user presentation. Faithfulness matters: the
message must not invent numbers/facts that are absent from the input.
"""
from __future__ import annotations

PROMPT_VERSION = "present-messages/v1"

_SYSTEM = (
    "あなたはデータを分かりやすい文章にまとめるアシスタントです。"
    "与えられたデータに存在する事実のみを用い、数値や事実を創作しないでください。"
)

_STYLES = {
    "brief": "3〜4文で簡潔に。",
    "detailed": "重要点を段落でまとめる。",
    "bullet": "箇条書きで要点を列挙する。",
}


def build_messages(data: str, *, fmt: str = "json", language: str = "ja",
                   audience: str | None = None, style: str = "brief",
                   instructions: str | None = None) -> list[dict]:
    """Build the chat messages for presentation. `data` is a JSON or TSV string."""
    lang = {"ja": "日本語", "en": "English"}.get(language, language)
    parts = [
        f"次の{fmt.upper()}データを{lang}で説明してください。",
        _STYLES.get(style, ""),
    ]
    if audience:
        parts.append(f"読み手: {audience}。")
    if instructions:
        parts.append(instructions)
    parts.append("データに無い数値・事実は書かないでください。")
    parts.append(f"\n--- データ ({fmt}) ---\n{data}")
    user = "\n".join(p for p in parts if p)
    return [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM}]},
        {"role": "user", "content": [{"type": "text", "text": user}]},
    ]


def present(data: str, *, fmt: str = "json", language: str = "ja",
            audience: str | None = None, style: str = "brief",
            instructions: str | None = None, llm=None,
            max_new_tokens: int = 1024) -> str:
    from ..llm import get_llm

    llm = llm or get_llm()
    messages = build_messages(data, fmt=fmt, language=language, audience=audience,
                              style=style, instructions=instructions)
    # A little sampling is fine for prose.
    return llm.generate(messages, do_sample=True, max_new_tokens=max_new_tokens)
