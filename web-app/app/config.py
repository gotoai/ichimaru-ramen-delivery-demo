"""Configuration for the web-app.

Loads ``web-app/.env`` and resolves the paths and upstream-service settings the app
needs. Deliberately tiny and import-safe: no pandas/shapely here, so importing config
(e.g. from tests) is cheap and side-effect free apart from reading .env.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# web-app/ (this file is app/config.py -> parents[1] is web-app/)
BASE_DIR = Path(__file__).resolve().parents[1]

# Load .env from web-app/.env if present (silent no-op otherwise).
load_dotenv(BASE_DIR / ".env")


def _path(env: str, default: Path) -> Path:
    val = os.getenv(env)
    return Path(val).expanduser().resolve() if val else default


# The pipeline's DATA/ tree. Default: repo's DATA/ (web-app/ sibling).
DATA_DIR: Path = _path("DATA_DIR", (BASE_DIR.parent / "DATA").resolve())

# Generated GeoJSON cache (prefecture outlines) lives inside web-app/.
GEOJSON_DIR: Path = BASE_DIR / "data" / "geojson"

# Static brand assets (favicon, etc.).
ASSETS_DIR: Path = BASE_DIR / "assets"

# Upstream Gemma host (agent-service) and its bearer key.
AGENT_SERVICE_URL: str = os.getenv("AGENT_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_API_KEY: str = os.getenv("GOTOAI_AGENT_API_KEY", "")

# This app's own bind address. Default 0.0.0.0 so other machines on the same LAN can
# reach it at http://<this-host-LAN-IP>:<WEB_PORT>. Set WEB_HOST=127.0.0.1 to restrict
# to localhost only.
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT: int = int(os.getenv("WEB_PORT", "8080"))

UI_LANGUAGE: str = os.getenv("UI_LANGUAGE", "ja")

# --- Individual data files under DATA_DIR (single source of truth for paths) ---
STORE_TSV = DATA_DIR / "s03_primary" / "store.tsv"
CALIBRATED_SALES_TSV = DATA_DIR / "s09_calibration" / "calibrated_sales.tsv"
CALIBRATION_INFO_JSON = DATA_DIR / "s09_calibration" / "calibration_info.json"
ESTIMATED_EVENTS_JSON = DATA_DIR / "s09_calibration" / "estimated_events.json"
SHAP_LONG_TSV = DATA_DIR / "s06_prediction" / "shap_values_long.tsv"
WEATHER_FORECAST_TSV = DATA_DIR / "s08_search" / "weather_forecast.tsv"

# Prefecture codes present in this demo -> geoshape_<NN> directories.
# (11 埼玉県, 12 千葉県, 13 東京都, 14 神奈川県)
GEOSHAPE_DIR = DATA_DIR / "s02_intermediate"
PREFECTURE_CODES = ("11", "12", "13", "14")
