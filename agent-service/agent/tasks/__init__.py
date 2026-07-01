"""Task modules — one capability each (prompt + call + parse/post-process).

Each module exposes a pure `build_messages(...)` / `parse_*(...)` pair (torch-free,
unit-testable offline) and a runner that calls the shared LLM. These are the units the
web API will wrap as `/v1/...` endpoints later.
"""
