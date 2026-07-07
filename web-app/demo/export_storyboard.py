"""Export beats.py to output/storyboard.json for the Node recorder (record_tour.js).

beats.py stays the single source of truth (Python); the Node tour can't import it, so this
dumps the config + beats to JSON. Run by `make record` before the Node script.
"""
from __future__ import annotations

import json
import os

import beats

os.makedirs("output", exist_ok=True)
data = {
    "url": beats.DEMO_URL,
    "store": beats.STORE,
    "viewport": beats.VIEWPORT,
    "chat_question": beats.CHAT_QUESTION,
    "beats": beats.BEATS,
}
with open("output/storyboard.json", "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
print("wrote output/storyboard.json")
