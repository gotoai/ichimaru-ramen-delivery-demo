"""Tolerant JSON extraction from LLM text output.

Small OSS models often wrap JSON in prose or ```json fences. These helpers pull the
first JSON array/object out of a reply. (When you move to constrained/guided decoding,
this becomes a safety net rather than the primary parser.)
"""
from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _candidate(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def parse_json_array(text: str) -> list:
    """Best-effort parse of a JSON array from `text`; [] if none/invalid."""
    for chunk in _fenced_then_raw(text, "[", "]"):
        try:
            val = json.loads(chunk)
            if isinstance(val, list):
                return val
        except json.JSONDecodeError:
            continue
    return []


def parse_json_object(text: str) -> dict:
    """Best-effort parse of a JSON object from `text`; {} if none/invalid."""
    for chunk in _fenced_then_raw(text, "{", "}"):
        try:
            val = json.loads(chunk)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            continue
    return {}


def _fenced_then_raw(text: str, open_ch: str, close_ch: str):
    """Yield candidate JSON substrings: fenced blocks first, then the raw span."""
    for m in _FENCE.finditer(text):
        yield m.group(1).strip()
    raw = _candidate(text, open_ch, close_ch)
    if raw is not None:
        yield raw
