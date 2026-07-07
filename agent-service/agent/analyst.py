"""The Ichimaru analytics agent — an agentic tool-using loop.

Answers demand/forecast/accuracy questions by querying the s10_analysis DuckDB layer
(run_sql) and the sandboxed shell (run_bash), iterating tool calls until it produces a
final answer. Shared by:

  * the API endpoint POST /v1/chat (non-streaming — a multi-step tool loop cannot stream), and
  * the interactive REPL agent/chatbot.py.

Torch-free: the tool-aware generation lives in llm.GemmaLLM.generate_tools; this module
only orchestrates (so importing it does not import torch).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config
from .tools.bash_tool import run_bash
from .tools.sql_tool import run_sql

PROMPT_VERSION = "analyst/v1"
MAX_TOOL_CALLS = 8
STOP_IDS = (1, 49, 106)  # <eos>, <tool_call|>, <turn|>

# A tool call renders as: <|tool_call>call:NAME{key:<|"|>value<|"|>, ...}<tool_call|>
_ARG_RE = re.compile(r'(\w+):<\|"\|>(.*?)<\|"\|>', re.DOTALL)

# The model sometimes announces it will run a query but ends the turn without emitting the
# tool call ("…少々お待ちください。"). Detect that so we can nudge it to actually act.
_PROMISE_RE = re.compile(
    r"(お待ち\s*(?:ください|下さい)|少々お待ち|これから.{0,8}(?:実行|調べ|分析|確認)|"
    r"実行しますので|次に.{0,8}(?:実行|クエリ)|let me run|i['’ ]?ll run|please wait|hold on)",
    re.IGNORECASE)

SQL_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": (
            "Run a DuckDB SQL query against the Ichimaru analytics tables (s10_analysis) "
            "and return a Markdown table. Views: dim_store, dim_date, dim_feature, "
            "fact_forecast, fact_actuals, fact_backtest, fact_shap, mart_store_scorecard, "
            "mart_forecast_interval, mart_anomaly. Prefer this for any data question. Add a LIMIT."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A DuckDB SQL query."}},
            "required": ["query"],
        },
    },
}
BASH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": (
            "Execute a bash command in the same sandbox (no network, read-only /data). Use "
            "for non-SQL needs like `date` (current date/time) or listing files."
        ),
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The bash command to run."}},
            "required": ["command"],
        },
    },
}
TOOLS = {"run_sql": run_sql, "run_bash": run_bash}
TOOL_SCHEMAS = [SQL_TOOL_SCHEMA, BASH_TOOL_SCHEMA]


def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "DATA").is_dir():
            return p
    return Path(__file__).resolve().parents[2]


def _load_schema_doc() -> str:
    p = _repo_root() / "DATA" / "s10_analysis" / "SCHEMA.md"
    return p.read_text(encoding="utf-8") if p.exists() else \
        "(SCHEMA.md not built yet — run `make analysis` in pipeline/.)"


SYSTEM_PROMPT = (
    "あなたはイチマルラーメンの需要分析アシスタントです。エリアマネージャーの質問に、"
    "記憶からではなく必ず**データを問い合わせて**答えます。\n\n"
    "利用可能なツール:\n"
    "- run_sql(query): 分析テーブル（s10_analysis）に DuckDB SQL を実行し、結果を表で返します。"
    "データに関する質問は必ずこれを使います。大きな結果には LIMIT を付けてください。\n"
    "- run_bash(command): 非SQL用（例: 今日の日付は `date`、ファイル一覧など）。\n\n"
    "利用可能なビュー: dim_store, dim_date, dim_feature, fact_forecast, fact_actuals, "
    "fact_backtest, fact_shap, fact_weather_forecast, mart_store_scorecard, "
    "mart_forecast_interval, mart_anomaly。\n"
    "数値は必ずクエリで確認してから、ユーザーの言語で簡潔に、根拠の数字を添えて答えてください。\n"
    "重要: 各質問は1回のやり取りで完結します。ツールが必要なら**今すぐ**呼び出してください。"
    "『これから実行します』『少々お待ちください』のように予告してターンを終えてはいけません"
    "（次のターンはありません）。ツールを呼ぶか、最終回答をするかのどちらかにしてください。\n\n"
    "--- 分析のヒント（重要） ---\n"
    "- 予測の要因（SHAP）は fact_shap にあり、label_ja・family・shap_value・feature_value・"
    "base_value・predicted を**すべて含みます**。dim_feature との結合は不要です。\n"
    "- fact_shap は『予測』（reference_date は2種のみ）を説明します。過去のバックテスト日の"
    "SHAP はありません。SHAP は『予測の要因』であって『誤差の要因』ではありません。\n"
    "- 『予測と実績のズレ（誤差）が大きい理由』は fact_backtest（residual=予測−実績, "
    "temp_gap=モデル気温−実気温, model_* と actual_* の天候）と mart_store_scorecard"
    "（bias_bowls, bias_direction, bias_slope）で分析します。誤差は主に気温プロキシのズレと"
    "バイアスで説明されます。\n"
    "- SQLは**DuckDB方言**です（SQLite/MySQLではありません）。日付: 今日=current_date、"
    "相対=`col - INTERVAL '7' DAY`、週=date_trunc('week', col)、抽出=EXTRACT(dow FROM col)、"
    "書式=strftime(col,'%Y-%m-%d')。`date('now', ...)`・DATEDIFF・NOW() は使えません。\n"
    "- 分析データは現在の週まで届かないことがあります。『先週』などは、まず "
    "`SELECT max(target_date) FROM fact_backtest WHERE store_name='…'` で最新期間を確認し、"
    "そこから遡って絞り込んでください（実データ範囲外を指定すると空になります）。\n"
    "例:\n"
    "  予測要因 → SELECT label_ja, round(shap_value,1) s FROM fact_shap "
    "WHERE store_name='…' AND target_date='2026-07-06' ORDER BY abs(shap_value) DESC LIMIT 8;\n"
    "  誤差要因 → SELECT target_date, residual, temp_gap, actual_rain_mm FROM fact_backtest "
    "WHERE store_name='…' ORDER BY abs(residual) DESC LIMIT 5;\n"
    "- 天気予報は fact_weather_forecast（summary=天気概況, precip_prob_pct=降水確率, "
    "high_temp_c, low_temp_c, rain_mm）。店舗の天気は dim_store と (prefecture, municipality) "
    "で結合します。\n"
    "  天気 → SELECT w.target_date, w.summary, w.precip_prob_pct, w.high_temp_c "
    "FROM dim_store s JOIN fact_weather_forecast w USING (prefecture, municipality) "
    "WHERE s.store_name='…' ORDER BY w.target_date;\n"
    "- 予測区間 mart_forecast_interval は**補正後（calibrated）基準**です。p50 = "
    "fact_forecast.calibrated（補正後の予測値）、p10/p90 = calibrated ± 過去残差の分位"
    "（バイアス除去済み）、order_sl90 = 90%サービス水準の発注目安（補正後基準）。"
    "補正前（raw predicted）ではありません。発注量の質問にはこの補正後の値を使ってください。\n\n"
    "--- データ辞書 (s10_analysis/SCHEMA.md) ---\n" + _load_schema_doc()
)


def parse_tool_call(raw_text: str):
    if "<|tool_call>" not in raw_text:
        return None
    m = re.search(r"call:(\w+)", raw_text)
    return (m.group(1), dict(_ARG_RE.findall(raw_text))) if m else None


def _clean(raw_text: str) -> str:
    return re.sub(r"<\|?[a-z_]+\|?>", "", raw_text).replace('<|"|>', "").strip()


def _text_msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def build_messages(message: str, history: list[dict] | None = None,
                   context: str | None = None) -> list[dict]:
    msgs = [_text_msg("system", SYSTEM_PROMPT)]
    for turn in history or []:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        if turn.get("text"):
            msgs.append(_text_msg(role, turn["text"]))
    if context:
        msgs.append(_text_msg("user", "【参考】現在の画面のデータ:\n" + context))
    msgs.append(_text_msg("user", message))
    return msgs


def answer(message: str, *, history: list[dict] | None = None, context: str | None = None,
           llm=None, max_new_tokens: int | None = None,
           max_tool_calls: int = MAX_TOOL_CALLS, on_step=None) -> dict:
    """Run the agentic loop for one question; return {message, steps, tool_calls}.

    `steps` is the tool transcript [{tool, input, output}, ...]. `on_step(step)` (optional)
    is called live as each tool runs (used by the REPL for progress printing).
    """
    if llm is None:
        from .llm import get_llm
        llm = get_llm()

    messages = build_messages(message, history, context)
    steps: list[dict] = []
    nudges_left = 2

    for _ in range(max_tool_calls):
        raw = llm.generate_tools(messages, TOOL_SCHEMAS, max_new_tokens=max_new_tokens,
                                 stop_ids=STOP_IDS)
        call = parse_tool_call(raw)

        if call is None:  # plain text
            ans = _clean(raw)
            messages.append(_text_msg("assistant", ans))
            if nudges_left > 0 and _PROMISE_RE.search(ans):
                nudges_left -= 1
                messages.append(_text_msg("user",
                    "予告や待機の返答はせず、必要なツール（run_sql など）を今すぐ実行し、"
                    "その結果に基づいて回答してください。"))
                continue
            return {"message": ans, "steps": steps, "tool_calls": len(steps)}

        name, args = call
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"type": "function",
                                         "function": {"name": name, "arguments": args}}]})
        fn = TOOLS.get(name)
        if fn is None:
            result = f"[unknown tool: {name}]"
        else:
            try:
                result = fn(**args)
            except TypeError as exc:
                result = f"[tool argument error: {exc}]"
        step = {"tool": name, "input": args, "output": result}
        steps.append(step)
        if on_step:
            on_step(step)
        messages.append({"role": "tool", "name": name, "content": result})

    return {"message": "[stopped: reached the tool-call limit for this turn]",
            "steps": steps, "tool_calls": len(steps)}
