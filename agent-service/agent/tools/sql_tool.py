#!/usr/bin/env python3
"""
sql_tool.py — run_sql: the analytics query surface over the s10_analysis DuckDB layer.

A thin wrapper over the sandboxed shell (bash_tool): it `cd`s into the analytics
directory, loads the typed views from schema.sql, and executes the model's SQL against
them — returning a Markdown table. The model just writes SQL; it never needs to know the
duckdb invocation, file paths, or schema loading.

Same isolation as bash_tool (no network, read-only data, disposable sandbox), so tool
calls need no approval. Available views: dim_store, dim_date, dim_feature, fact_forecast,
fact_actuals, fact_backtest, fact_shap, mart_store_scorecard, mart_forecast_interval,
mart_anomaly (see /data/DATA/s10_analysis/SCHEMA.md).
"""
from . import bash_tool

_HEREDOC = "__ICHI_SQL__"

# Folded-in "grammar help": on a DuckDB error, append a targeted hint so the model can
# self-correct on the next step (cheaper and more useful than a separate validator tool,
# since DuckDB already reports the exact error when the query runs).
_DIALECT_HINT = (
    "\n[hint] This is DuckDB (not SQLite/MySQL). Dates: today = current_date; "
    "relative = col - INTERVAL '7' DAY; week bucket = date_trunc('week', col); "
    "extract = EXTRACT(dow FROM col); format = strftime(col, '%Y-%m-%d'). "
    "date('now', ...), DATEDIFF, NOW() are NOT supported. Note: the analytics data may not "
    "reach the current week — check `SELECT max(target_date) FROM <table> WHERE ...` first."
)
_COLUMN_HINT = (
    "\n[hint] Check exact table/column names in SCHEMA.md. For SHAP use fact_shap directly "
    "— it already contains label_ja, family, shap_value, feature_value, base_value, "
    "predicted (do NOT join dim_feature; shap_value is NOT in dim_feature)."
)


def run_sql(query: str) -> str:
    """Execute `query` against the s10_analysis DuckDB views; return a Markdown table.

    The SQL is passed via a quoted heredoc (no shell interpolation), so it may contain any
    quotes/pipes/newlines. Add your own LIMIT for large results — output is truncated.
    On a DuckDB error, a short dialect/column hint is appended to aid self-correction.
    """
    body = ".read schema.sql\n.mode markdown\n" + (query or "").strip()
    command = (
        "cd /data/DATA/s10_analysis && "
        f"duckdb :memory: <<'{_HEREDOC}'\n{body}\n{_HEREDOC}\n"
    )
    out = bash_tool.run_bash(command)
    if "Parser Error" in out:
        out += _DIALECT_HINT
    elif "Binder Error" in out or "Catalog Error" in out:
        out += _COLUMN_HINT
    return out


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "SELECT store_name, mape_pct, bias_direction FROM mart_store_scorecard ORDER BY mape_pct DESC LIMIT 5;"
    print(run_sql(q))
