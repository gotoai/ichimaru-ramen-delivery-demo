"""In-memory data layer over the pipeline's calibration outputs.

Loads the calibrated-sales chain and its explanation sources ONCE at startup and
serves them through small accessors. The AI never computes these numbers — the
pipeline already did (each row carries ``self_check_ok``) — so the app only reads and
presents them.

Sources (all under ``config.DATA_DIR``):
  * s09_calibration/calibrated_sales.tsv   — the number managers act on, with the
    predicted -> weather -> event chain already broken out per row.
  * s09_calibration/calibration_info.json  — per-row *why*: formula, weather factors,
    events, self-check.
  * s06_prediction/shap_values_long.tsv    — per-feature SHAP contributions (the
    forecast underneath the calibration).
  * s09_calibration/estimated_events.json  — nearby events with attendance + rationale.
  * s03_primary/store.tsv                  — store coordinates for the map.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from . import config

_JST = ZoneInfo("Asia/Tokyo")

# Japanese weekday labels (Mon=0 .. Sun=6), for display in tables/tooltips.
_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def tomorrow_jst() -> str:
    """ISO date of the day after today in JST — the forecast always starts here."""
    return (datetime.now(_JST).date() + timedelta(days=1)).isoformat()

# Human-readable labels for the chain columns (used by the UI and the chat context).
CHAIN_LABELS = {
    "predicted_sales": "予測売上",
    "weather_calibrated_sales": "天候補正後",
    "event_added_demand": "イベント増加分",
    "calibrated_sales": "補正後売上",
}

# Friendly Japanese labels for the 30 model features, so the AI can explain SHAP
# contributions in plain language instead of technical column names.
FEATURE_LABELS = {
    "is_weekend": "週末かどうか",
    "weekday_number": "曜日",
    "month_number": "月",
    "target_offdays_cos": "季節性（周期・cos）",
    "target_offdays_sin": "季節性（周期・sin）",
    "week-1_avg_sales": "先週の平均売上",
    "week-1_median_sales": "先週の中央値売上",
    "week-1_avg_weekday_sales": "先週の平日平均売上",
    "week-1_avg_weekend_sales": "先週の週末平均売上",
    "week-1_median_weekday_sales": "先週の平日中央値売上",
    "week-1_median_weekend_sales": "先週の週末中央値売上",
    "week-1to4_avg_sales": "過去4週の平均売上",
    "week-1to4_median_sales": "過去4週の中央値売上",
    "week-1to4_avg_weekday_sales": "過去4週の平日平均売上",
    "week-1to4_avg_weekend_sales": "過去4週の週末平均売上",
    "week-1to4_median_weekday_sales": "過去4週の平日中央値売上",
    "week-1to4_median_weekend_sales": "過去4週の週末中央値売上",
    "delta-week-1to4_avg_sales": "直近1週と過去4週平均の差（全体）",
    "delta-week-1to4_weekday_avg_sales": "直近1週と過去4週平均の差（平日）",
    "delta-week-1to4_weekend_avg_sales": "直近1週と過去4週平均の差（週末）",
    "week-1to4_weekday1_avg_sales": "過去4週の月曜平均売上",
    "week-1to4_weekday2_avg_sales": "過去4週の火曜平均売上",
    "week-1to4_weekday3_avg_sales": "過去4週の水曜平均売上",
    "week-1to4_weekday4_avg_sales": "過去4週の木曜平均売上",
    "week-1to4_weekday5_avg_sales": "過去4週の金曜平均売上",
    "week-1to4_weekday6_avg_sales": "過去4週の土曜平均売上",
    "week-1to4_weekday7_avg_sales": "過去4週の日曜平均売上",
    "week+1_high_temperature": "予測日の最高気温",
    "week+1_avg_temperature": "予測日の平均気温",
    "week+1_rainfall": "予測日の降水量",
}


def _weekday_ja(iso_date: str) -> str:
    y, m, d = (int(x) for x in iso_date.split("-"))
    return _WEEKDAY_JA[date(y, m, d).weekday()]


def extract_municipality(prefecture: str, store_name: str) -> str:
    """The store's 市区町村, matching the weather forecast's granularity.

    Same rule as the pipeline (`search-events` / `calibrate-for-weather`): strip the
    prefecture prefix, then take up to and including the first 市 (from index 1, so
    name-initial 市原市/市川市 aren't mis-cut); else the first 区 (東京特別区); else the
    first 町/村. Drops 政令市の行政区. The forecast join key is (prefecture, 市区町村,
    target_date)."""
    rest = store_name[len(prefecture):] if store_name.startswith(prefecture) else store_name
    for ch in ("市", "区"):
        i = rest.find(ch, 1)
        if i != -1:
            return rest[: i + 1]
    cands = [k for k in (rest.find("町", 1), rest.find("村", 1)) if k != -1]
    if cands:
        return rest[: min(cands) + 1]
    return rest


class Data:
    """Loaded-once view of the calibration outputs."""

    def __init__(self) -> None:
        self._load()

    def _load(self) -> None:
        cal = pd.read_csv(config.CALIBRATED_SALES_TSV, sep="\t", dtype={"reference_date": str, "target_date": str})
        self._cal = cal

        # Freshest forecast per (store, target_date): the row from the most recent
        # reference_date that covers that day. This lets the UI show a rolling window
        # starting tomorrow, spanning reference dates, instead of one fixed ref week.
        self._fresh = (cal.sort_values("reference_date")
                          .drop_duplicates(subset=["store_name", "target_date"], keep="last"))
        # (store, target) -> reference_date, so breakdown/SHAP can find the right ref.
        self._ref_by_key = {
            (r["store_name"], r["target_date"]): r["reference_date"]
            for _, r in self._fresh.iterrows()
        }

        # Stores: coordinates from store.tsv, keyed by name.
        store = pd.read_csv(config.STORE_TSV, sep="\t")
        self._store = store.set_index("store_name")[["prefecture", "latitude", "longitude"]]

        # calibration_info: index by (store, ref, target).
        with open(config.CALIBRATION_INFO_JSON, encoding="utf-8") as fh:
            info = json.load(fh)
        self._info: dict[tuple[str, str, str], dict] = {
            (r["store_name"], r["reference_date"], r["target_date"]): r for r in info
        }

        # SHAP long -> per (store, ref, target) list sorted by |shap| desc.
        shap = pd.read_csv(config.SHAP_LONG_TSV, sep="\t", dtype={"reference_date": str, "target_date": str})
        self._shap = shap

        # Events with attendance + rationale + geo.
        with open(config.ESTIMATED_EVENTS_JSON, encoding="utf-8") as fh:
            self._events: list[dict] = json.load(fh)

        # Weather forecast: keyed by (prefecture, 市区町村) -> rows sorted by target_date.
        self._weather: dict[tuple[str, str], list[dict]] = {}
        if config.WEATHER_FORECAST_TSV.exists():
            wf = pd.read_csv(config.WEATHER_FORECAST_TSV, sep="\t", dtype=str).fillna("")
            for (pref, muni), grp in wf.groupby(["prefecture", "shikuchoson"]):
                self._weather[(pref, muni)] = (
                    grp.sort_values("target_date").to_dict("records"))

    # ---- rolling window --------------------------------------------------
    def _window(self, store_name: str, days: int = 7) -> pd.DataFrame:
        """Freshest rows for a store over the next `days` days starting tomorrow (JST),
        sorted by target_date. Falls back to the latest `days` available if the horizon
        is entirely in the past (e.g. run outside the demo's date range)."""
        f = self._fresh[self._fresh["store_name"] == store_name].sort_values("target_date")
        upcoming = f[f["target_date"] >= tomorrow_jst()]
        rows = upcoming if not upcoming.empty else f
        return rows.head(days)

    # ---- stores ----------------------------------------------------------
    def stores(self) -> list[dict[str, Any]]:
        """Stores with coordinates and their mean calibrated demand over the upcoming
        window (for the map: marker position + colour/size)."""
        out = []
        for name, row in self._store.iterrows():
            win = self._window(name)
            if win.empty:
                continue
            out.append({
                "store_name": name,
                "prefecture": row["prefecture"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "mean_calibrated": round(float(win["calibrated_sales"].mean())),
                "event_count": int(win["event_count"].max()),
            })
        return sorted(out, key=lambda s: s["store_name"])

    def store_names(self) -> list[str]:
        return sorted(self._store.index.tolist())

    def has_store(self, name: str) -> bool:
        return name in self._store.index

    # ---- forecast series -------------------------------------------------
    def forecast(self, store_name: str, days: int = 7) -> list[dict[str, Any]]:
        """Rolling forecast: the next `days` days starting tomorrow (JST), sorted by
        target_date. All displayed estimates are rounded to whole bowls."""
        out = []
        for _, r in self._window(store_name, days).iterrows():
            out.append({
                "target_date": r["target_date"],
                "reference_date": r["reference_date"],
                "weekday": _weekday_ja(r["target_date"]),
                "predicted_sales": round(float(r["predicted_sales"])),
                "weather_calibrated_sales": round(float(r["weather_calibrated_sales"])),
                "event_added_demand": round(float(r["event_added_demand"])),
                "event_count": int(r["event_count"]),
                "calibrated_sales": round(float(r["calibrated_sales"])),
                # Order quantity is the manager's call; expose the rounded-up bowls as a hint.
                "suggested_order": math.ceil(float(r["calibrated_sales"])),
            })
        return out

    def _ref_for(self, store_name: str, target_date: str) -> str | None:
        return self._ref_by_key.get((store_name, target_date))

    def shap_explanation(self, store_name: str, target_date: str | None = None,
                         n: int = 6) -> dict[str, Any] | None:
        """Top-`n` SHAP drivers of the forecast (default day = tomorrow), with friendly
        Japanese labels — the payload the AI turns into a plain-language explanation."""
        win = self._window(store_name)
        if win.empty:
            return None
        if not target_date:
            target_date = win.iloc[0]["target_date"]
        ref = self._ref_for(store_name, target_date)
        if not ref:
            return None
        s = self._shap
        rows = s[(s["store_name"] == store_name) & (s["reference_date"] == ref)
                 & (s["target_date"] == target_date)]
        if rows.empty:
            return None
        base = float(rows.iloc[0]["base_value"])
        predicted = float(rows.iloc[0]["predicted_sales"])
        trip = [(r["feature"], float(r["feature_value"]), float(r["shap_value"]))
                for _, r in rows.iterrows()]
        trip.sort(key=lambda t: abs(t[2]), reverse=True)
        return {
            "store_name": store_name,
            "target_date": target_date,
            "weekday": _weekday_ja(target_date),
            "基準値_杯": round(base),
            "予測売上_杯": round(predicted),
            "上位要因": [
                {"要因": FEATURE_LABELS.get(f, f),
                 "特徴量の値": round(fv, 1),
                 "予測への寄与_杯": round(sv)}
                for f, fv, sv in trip[:n]
            ],
        }

    # ---- per-target breakdown (waterfall + factors + top SHAP) -----------
    def breakdown(self, store_name: str, target_date: str) -> dict[str, Any] | None:
        ref = self._ref_for(store_name, target_date)
        info = self._info.get((store_name, ref, target_date)) if ref else None
        if info is None:
            return None
        top_shap = self._top_shap(store_name, ref, target_date, n=6)
        return {
            "store_name": store_name,
            "reference_date": ref,
            "target_date": target_date,
            "weekday": _weekday_ja(target_date),
            "predicted_sales": round(float(info["predicted_sales"])),
            "weather_calibrated_sales": round(float(info["weather_calibrated_sales"])),
            "event_added_demand": round(float(info["event_added_demand"])),
            "calibrated_sales": round(float(info["calibrated_sales"])),
            "formula": info.get("formula", ""),
            "weather": info.get("weather", {}),
            "events": info.get("events", []),
            "self_check_ok": bool(info.get("self_check_ok", False)),
            "top_shap": top_shap,
        }

    def shap_waterfall(self, store_name: str, target_date: str | None = None,
                       top_n: int = 8) -> dict[str, Any] | None:
        """SHAP decomposition of the week+1 forecast for a store on `target_date`
        (default: the first upcoming day = tomorrow), shaped for a waterfall chart:
        base_value -> top-N feature contributions (+ aggregated 'other') -> prediction.

        Values are raw floats so ``base + Σcontrib == predicted`` exactly; the UI rounds
        them for display."""
        win = self._window(store_name)
        if win.empty:
            return None
        if not target_date:
            target_date = win.iloc[0]["target_date"]
        ref = self._ref_for(store_name, target_date)
        if not ref:
            return None
        s = self._shap
        rows = s[(s["store_name"] == store_name) & (s["reference_date"] == ref)
                 & (s["target_date"] == target_date)]
        if rows.empty:
            return None

        base = float(rows.iloc[0]["base_value"])
        predicted = float(rows.iloc[0]["predicted_sales"])
        pairs = [(r["feature"], float(r["shap_value"])) for _, r in rows.iterrows()]
        pairs.sort(key=lambda kv: abs(kv[1]), reverse=True)
        top, rest = pairs[:top_n], pairs[top_n:]
        return {
            "store_name": store_name,
            "target_date": target_date,
            "weekday": _weekday_ja(target_date),
            "base_value": base,
            "predicted": predicted,
            "items": [{"feature": f, "shap": v} for f, v in top],
            "other": sum(v for _, v in rest),
        }

    def _top_shap(self, store_name: str, ref: str, target_date: str, n: int) -> list[dict[str, Any]]:
        s = self._shap
        rows = s[(s["store_name"] == store_name) & (s["reference_date"] == ref) & (s["target_date"] == target_date)]
        if rows.empty:
            return []
        rows = rows.reindex(rows["shap_value"].abs().sort_values(ascending=False).index).head(n)
        return [{
            "feature": r["feature"],
            "feature_value": round(float(r["feature_value"])),
            "shap_value": round(float(r["shap_value"])),
        } for _, r in rows.iterrows()]

    # ---- events ----------------------------------------------------------
    def events(self) -> list[dict[str, Any]]:
        """All estimated events with geo + attendance (map overlay)."""
        out = []
        for e in self._events:
            att = e.get("expected_attendance") or {}
            out.append({
                "store_name": e.get("store_name"),
                "event_name": e.get("event_name"),
                "event_type": e.get("event_type"),
                "start_date": e.get("start_date"),
                "end_date": e.get("end_date"),
                "venue": e.get("venue"),
                "latitude": e.get("latitude"),
                "longitude": e.get("longitude"),
                "distance_m": e.get("distance_m"),
                "attendance_point": att.get("point"),
                "attendance_confidence": e.get("attendance_confidence"),
                "rationale": e.get("rationale"),
                "source_url": e.get("source_url"),
            })
        return [e for e in out if e["latitude"] is not None and e["longitude"] is not None]

    def events_for_store(self, store_name: str) -> list[dict[str, Any]]:
        return [e for e in self.events() if e["store_name"] == store_name]

    # ---- weather forecast ------------------------------------------------
    def weather_for_store(self, store_name: str, days: int = 7) -> list[dict[str, Any]]:
        """Weather forecast for a store's 市区町村 over the upcoming window (from tomorrow).

        Maps the store to its municipality with the pipeline's rule and joins the forecast
        on (prefecture, 市区町村, target_date). Empty if the store or its dates aren't
        covered by the forecast."""
        if store_name not in self._store.index:
            return []
        pref = self._store.loc[store_name, "prefecture"]
        muni = extract_municipality(pref, store_name)
        rows = self._weather.get((pref, muni))
        if not rows:
            return []
        start = tomorrow_jst()
        out = []
        for r in rows:
            if r["target_date"] < start:
                continue
            out.append({
                "target_date": r["target_date"],
                "weekday": _weekday_ja(r["target_date"]),
                "天気概況": r.get("天気概況", ""),
                "降水確率%": r.get("降水確率(%)", ""),
                "最高気温": r.get("最高気温", ""),
                "最低気温": r.get("最低気温", ""),
                "降水量mm": r.get("推定日降水量(mm)", ""),
            })
            if len(out) >= days:
                break
        return out


@lru_cache(maxsize=1)
def get_data() -> Data:
    """Process-wide singleton; loads on first access."""
    return Data()
