"""Storyboard for the 1-minute demo video: the narration beats + on-screen actions.

Single source of truth shared by record_tour.py (paces the screen capture) and
prepare_audio.py (assembles narration.wav + timings + SRT). Edit the text/durations here.

Each beat: id, action (what the tour does), seconds (fallback duration if no TTS audio
is present yet), and text (the Japanese narration for that beat → TTS).
"""
from __future__ import annotations

import os

# The running web-app to record. Start it first (`make -C web-app dev`), and agent-service
# too if you want the live chat/explanation beats to show real answers.
DEMO_URL = os.getenv("DEMO_URL", "http://127.0.0.1:8080")

# Store to feature. None = auto-pick the highest-demand store (a dark-red marker).
STORE = os.getenv("DEMO_STORE") or None

VIEWPORT = {"width": 1600, "height": 900}

BEATS = [
    {
        "id": 1, "action": "intro", "seconds": 10,
        "text": "イチマル・ラーメンの、AI発注ダッシュボード。"
                "エリアマネージャーが各店舗の需要を確認し、発注量を決めるためのツールです。",
    },
    {
        "id": 2, "action": "map", "seconds": 12,
        "text": "各店舗は、補正後の予測需要で色分けされています。"
                "濃い赤ほど需要が高く、黄色い星印は近隣で開催されるイベントを示します。",
    },
    {
        "id": 3, "action": "forecast", "seconds": 14,
        "text": "店舗を選ぶと、明日から七日間の予測が表示されます。"
                "予測売上に天候とイベントの補正を加えた補正後売上と、発注の目安が一目でわかります。",
    },
    {
        "id": 4, "action": "shap", "seconds": 12,
        "text": "予測数量の分解では、AIが予測の根拠をウォーターフォール図で可視化し、"
                "主要な要因を分かりやすく説明します。",
    },
    {
        "id": 5, "action": "chat_weather", "seconds": 12,
        "text": "チャットでは、需要や天気をAIに質問できます。"
                "明日の天気は、と尋ねると、その場で最新の予報にもとづいて回答します。",
    },
]

# The question typed in the chat beat.
CHAT_QUESTION = "明日の天気は？"
