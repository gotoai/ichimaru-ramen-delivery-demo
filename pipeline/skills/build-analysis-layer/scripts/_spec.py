"""Table registry for the s10_analysis layer — the single column contract.

Drives the builders (column order), schema.sql (typed DuckDB views), and SCHEMA.md
(data dictionary), so all three can never drift. Types are DuckDB types; booleans are
INTEGER 0/1. See docs/analysis/s10_analysis_design.md.

Each table: dict(grain, layout, source, desc, columns=[(name, duckdb_type, note), ...]).
"""
from __future__ import annotations

# ---- core layer (dims + facts) -------------------------------------------------------
TABLES: dict[str, dict] = {
    "dim_store": {
        "grain": "store",
        "layout": "wide",
        "source": "s03/store.tsv + matched_store_weather_station.tsv + derived 市区町村",
        "desc": "Store master: location, sales baselines, matched JMA weather station.",
        "columns": [
            ("store_name", "TEXT", "natural key"),
            ("prefecture", "TEXT", ""),
            ("municipality", "TEXT", "市区町村 (same rule as calibrate-for-weather)"),
            ("latitude", "DOUBLE", ""),
            ("longitude", "DOUBLE", ""),
            ("weekday_baseline", "DOUBLE", "synthetic weekday sales baseline"),
            ("weekend_baseline", "DOUBLE", "synthetic weekend sales baseline"),
            ("station_number", "TEXT", "matched JMA station"),
            ("station_name", "TEXT", ""),
            ("station_distance_m", "DOUBLE", "store→station distance (m)"),
        ],
    },
    "dim_date": {
        "grain": "date",
        "layout": "wide",
        "source": "derived (calendar over the data span)",
        "desc": "Calendar spanning earliest actual sale → latest forecast target date.",
        "columns": [
            ("date", "DATE", "ISO"),
            ("year", "INTEGER", ""),
            ("month", "INTEGER", ""),
            ("day", "INTEGER", ""),
            ("weekday_number", "INTEGER", "1=Mon … 7=Sun"),
            ("weekday_ja", "TEXT", "月..日"),
            ("is_weekend", "INTEGER", "0/1 (Sat/Sun)"),
            ("iso_week", "INTEGER", ""),
        ],
    },
    "dim_feature": {
        "grain": "feature",
        "layout": "wide",
        "source": "derived (labels + families for the 30 model features)",
        "desc": "Friendly labels and family for each DFM feature (for SHAP explanation).",
        "columns": [
            ("feature", "TEXT", "raw model column name"),
            ("label_ja", "TEXT", "friendly Japanese label"),
            ("family", "TEXT", "lag_sales | calendar | weather"),
        ],
    },
    "fact_forecast": {
        "grain": "store × reference_date × target_date",
        "layout": "wide",
        "source": "s09/calibrated_sales.tsv + calibration_info.json (factors flattened)",
        "desc": "Primary serving table: the calibrated forecast chain + weather factors.",
        "columns": [
            ("store_name", "TEXT", ""),
            ("prefecture", "TEXT", ""),
            ("reference_date", "DATE", "Thursday the forecast was made"),
            ("target_date", "DATE", "forecasted day"),
            ("weekday_number", "INTEGER", ""),
            ("weekday_ja", "TEXT", ""),
            ("is_weekend", "INTEGER", "0/1"),
            ("predicted", "DOUBLE", "raw model prediction (bowls)"),
            ("weather_calibrated", "DOUBLE", "after weather calibration"),
            ("event_added_demand", "DOUBLE", "event uplift (bowls)"),
            ("event_count", "INTEGER", "nearby events applied"),
            ("calibrated", "DOUBLE", "final = weather_calibrated + event_added_demand"),
            ("weather_applied", "INTEGER", "0/1 — forecast weather available"),
            ("temp_gap", "DOUBLE", "feature_high − forecast_high (°C)"),
            ("forecast_high_temp_c", "DOUBLE", ""),
            ("forecast_rain_mm", "DOUBLE", ""),
            ("bias_slope", "DOUBLE", "per-store bias slope applied"),
            ("ht_band", "TEXT", "temperature band (none/above_5to10/below_5to10)"),
            ("ht_slope", "DOUBLE", "temperature calibration slope"),
            ("rf_band", "TEXT", "rainfall band"),
            ("rf_slope", "DOUBLE", "rainfall calibration slope"),
            ("self_check_ok", "INTEGER", "0/1 — calibration self-check passed"),
        ],
    },
    "fact_actuals": {
        "grain": "store × date",
        "layout": "wide",
        "source": "s03/sales.tsv (enriched with calendar)",
        "desc": "Historical actual daily sales (bowls) with calendar fields.",
        "columns": [
            ("store_name", "TEXT", ""),
            ("prefecture", "TEXT", ""),
            ("date", "DATE", ""),
            ("weekday_number", "INTEGER", ""),
            ("weekday_ja", "TEXT", ""),
            ("is_weekend", "INTEGER", "0/1"),
            ("actual_sales", "INTEGER", "bowls"),
        ],
    },
    "fact_backtest": {
        "grain": "store × reference_date × target_date",
        "layout": "wide",
        "source": "s07/residuals.tsv (Japanese columns ASCII-renamed)",
        "desc": "Back-test: predicted vs actual with model-vs-actual weather.",
        "columns": [
            ("store_name", "TEXT", ""),
            ("prefecture", "TEXT", ""),
            ("reference_date", "DATE", ""),
            ("target_date", "DATE", ""),
            ("weekday_number", "INTEGER", ""),
            ("weekday_ja", "TEXT", ""),
            ("is_weekend", "INTEGER", "0/1"),
            ("predicted", "DOUBLE", ""),
            ("actual_sales", "INTEGER", ""),
            ("residual", "DOUBLE", "predicted − actual"),
            ("model_high_temp_c", "DOUBLE", "model input (proxy) high temp"),
            ("model_avg_temp_c", "DOUBLE", ""),
            ("model_rain_mm", "DOUBLE", "0 by construction"),
            ("actual_high_temp_c", "DOUBLE", "real high temp on target date"),
            ("actual_avg_temp_c", "DOUBLE", ""),
            ("actual_rain_mm", "DOUBLE", "real rainfall"),
            ("temp_gap", "DOUBLE", "model_high − actual_high (°C)"),
        ],
    },
    "fact_shap": {
        "grain": "store × reference_date × target_date × feature",
        "layout": "long",
        "source": "s06/shap_values_long.tsv + dim_feature",
        "desc": "Per-feature SHAP contributions (tidy/long) with labels.",
        "columns": [
            ("store_name", "TEXT", ""),
            ("prefecture", "TEXT", ""),
            ("reference_date", "DATE", ""),
            ("target_date", "DATE", ""),
            ("feature", "TEXT", ""),
            ("label_ja", "TEXT", "from dim_feature"),
            ("family", "TEXT", ""),
            ("feature_value", "DOUBLE", ""),
            ("shap_value", "DOUBLE", "contribution (bowls); base+Σshap=predicted"),
            ("base_value", "DOUBLE", ""),
            ("predicted", "DOUBLE", ""),
        ],
    },
}

TABLES["fact_weather_forecast"] = {
    "grain": "prefecture × municipality × target_date",
    "layout": "wide",
    "source": "s08_search/weather_forecast.tsv (ASCII-renamed)",
    "desc": "Weather forecast per 市区町村/day. Join to stores via dim_store.municipality.",
    "columns": [
        ("prefecture", "TEXT", ""),
        ("municipality", "TEXT", "市区町村 (= dim_store.municipality)"),
        ("sub_region", "TEXT", "JMA sub-region (e.g. 東京地方)"),
        ("target_date", "DATE", ""),
        ("weekday_number", "INTEGER", ""),
        ("weekday_ja", "TEXT", ""),
        ("summary", "TEXT", "天気概況 (e.g. 曇時々晴)"),
        ("precip_prob_pct", "INTEGER", "降水確率 %"),
        ("high_temp_c", "INTEGER", "最高気温"),
        ("low_temp_c", "INTEGER", "最低気温"),
        ("rain_mm", "DOUBLE", "推定日降水量 mm"),
    ],
}

CORE_TABLES = list(TABLES.keys())

# ---- marts (derived analytics; see docs/analysis/s10_analysis_design.md §4) ----------
TABLES.update({
    "mart_store_scorecard": {
        "grain": "store",
        "layout": "wide",
        "source": "fact_backtest + fact_actuals + fact_forecast + s07/slope.tsv",
        "desc": "Per-store at-a-glance card: accuracy, bias, demand level/trend, events.",
        "columns": [
            ("store_name", "TEXT", ""),
            ("prefecture", "TEXT", ""),
            ("mae_bowls", "DOUBLE", "mean |predicted−actual| over backtest"),
            ("mape_pct", "DOUBLE", "mean |resid|/actual ×100"),
            ("bias_bowls", "DOUBLE", "mean residual (+ = over-forecast)"),
            ("bias_pct", "DOUBLE", "mean resid/actual ×100"),
            ("bias_direction", "TEXT", "over / under / neutral (|bias_pct|<3%)"),
            ("accuracy_rank", "INTEGER", "1 = lowest mape"),
            ("bias_slope", "DOUBLE", "diagnostic per-store slope (>1 over, <1 under)"),
            ("mean_actual_last28", "DOUBLE", "avg daily actual, last 28 days"),
            ("demand_change_pct", "DOUBLE", "(last28 − prev28)/prev28 ×100"),
            ("cv_actual_last28", "DOUBLE", "std/mean of daily actuals (volatility)"),
            ("mean_calibrated_next7", "DOUBLE", "avg calibrated over upcoming 7 days"),
            ("demand_rank", "INTEGER", "1 = highest mean_calibrated_next7"),
            ("weather_adj_next7_bowls", "DOUBLE", "avg (weather_calibrated−predicted) next7"),
            ("event_uplift_next7", "DOUBLE", "Σ event_added_demand over next7"),
            ("has_event_next7", "INTEGER", "0/1"),
        ],
    },
    "mart_forecast_interval": {
        "grain": "store × reference_date × target_date",
        "layout": "wide",
        "source": "fact_forecast (calibrated point) + fact_backtest (residual spread)",
        "desc": ("Prediction interval CENTERED ON THE CALIBRATED (補正後) forecast; band "
                 "width from de-biased historical residuals. p50 = fact_forecast.calibrated. "
                 "NOT based on the raw (補正前) prediction. Use for 発注量 / service level."),
        "columns": [
            ("store_name", "TEXT", ""),
            ("prefecture", "TEXT", ""),
            ("reference_date", "DATE", ""),
            ("target_date", "DATE", ""),
            ("calibrated", "DOUBLE", "補正後 point forecast (= fact_forecast.calibrated = p50)"),
            ("p10", "DOUBLE", "calibrated + q10 of de-biased backtest residuals (actual−raw_pred, median removed)"),
            ("p50", "DOUBLE", "= calibrated (補正後 median)"),
            ("p90", "DOUBLE", "calibrated + q90 (same de-biased residuals)"),
            ("order_sl90", "INTEGER", "発注目安 = calibrated + q90 (90% service level, 補正後基準, ceil)"),
            ("n_backtest", "INTEGER", "residual sample size used"),
        ],
    },
    "mart_anomaly": {
        "grain": "store × signal",
        "layout": "long",
        "source": "fact_actuals + fact_backtest",
        "desc": "Attention list: notable per-store signals (only flagged rows are emitted).",
        "columns": [
            ("store_name", "TEXT", ""),
            ("prefecture", "TEXT", ""),
            ("signal_type", "TEXT", "demand_shift | persistent_bias | accuracy_degradation | volatility_spike"),
            ("severity", "TEXT", "med / high"),
            ("value", "DOUBLE", "signal magnitude (meaning depends on signal_type)"),
            ("detail", "TEXT", "human-readable detail"),
            ("as_of", "DATE", "build date"),
        ],
    },
})

MART_TABLES = ["mart_store_scorecard", "mart_forecast_interval", "mart_anomaly"]
