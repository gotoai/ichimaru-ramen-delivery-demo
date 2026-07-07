#!/usr/bin/env python3
"""Build the s10_analysis MARTS from the core layer (design doc §4).

Reads the already-built core tables under DATA/s10_analysis/ (fact_backtest,
fact_actuals, fact_forecast) plus s07/slope.tsv, and writes:

  * mart_store_scorecard  — per-store accuracy / bias / demand level+trend / events.
  * mart_forecast_interval — de-biased empirical prediction interval + suggested order.
  * mart_anomaly           — attention list of notable per-store signals (long).

Run after build_core.py. Deterministic apart from the build-time JST date (used for the
"next 7 days" window and as_of). Uses pandas + numpy.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import find_repo_root, write_table  # noqa: E402

_JST = ZoneInfo("Asia/Tokyo")
SERVICE_LEVEL = 0.90
NEUTRAL_BIAS_PCT = 3.0
MIN_BACKTEST = 14


def _s10(root: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(root / "DATA/s10_analysis" / f"{name}.tsv", sep="\t")


def _tomorrow() -> str:
    return (datetime.now(_JST).date() + timedelta(days=1)).isoformat()


def _next7(ff: pd.DataFrame) -> pd.DataFrame:
    up = ff[ff["target_date"] >= _tomorrow()].sort_values(["store_name", "target_date"])
    return up.groupby("store_name", group_keys=False).head(7)


# --------------------------------------------------------------------------- scorecard
def build_scorecard(root: Path) -> None:
    bt = _s10(root, "fact_backtest")
    fa = _s10(root, "fact_actuals")
    ff = _s10(root, "fact_forecast")
    slope = pd.read_csv(root / "DATA/s07_diagnosis/slope.tsv", sep="\t")

    # Accuracy (per store, over the backtest window).
    g = bt.assign(abs_res=bt["residual"].abs(),
                  ape=bt["residual"].abs() / bt["actual_sales"],
                  bpe=bt["residual"] / bt["actual_sales"]).groupby(["store_name", "prefecture"])
    acc = g.agg(mae_bowls=("abs_res", "mean"), mape_pct=("ape", "mean"),
                bias_bowls=("residual", "mean"), bias_pct=("bpe", "mean")).reset_index()
    acc["mape_pct"] *= 100
    acc["bias_pct"] *= 100
    acc["bias_direction"] = np.where(acc["bias_pct"].abs() < NEUTRAL_BIAS_PCT, "neutral",
                                     np.where(acc["bias_pct"] > 0, "over", "under"))
    acc["accuracy_rank"] = acc["mape_pct"].rank(method="min").astype(int)
    acc = acc.merge(slope[["store_name", "bias_slope"]], on="store_name", how="left")

    # Demand level & trend (actuals: last 28 vs previous 28 days, anchored to data).
    fa = fa.copy()
    fa["d"] = pd.to_datetime(fa["date"])
    dmax = fa["d"].max()
    last28 = fa[fa["d"] > dmax - pd.Timedelta(days=28)]
    prev28 = fa[(fa["d"] <= dmax - pd.Timedelta(days=28)) & (fa["d"] > dmax - pd.Timedelta(days=56))]
    l = last28.groupby("store_name")["actual_sales"].agg(["mean", "std"])
    p = prev28.groupby("store_name")["actual_sales"].mean()
    dem = pd.DataFrame({
        "mean_actual_last28": l["mean"],
        "cv_actual_last28": l["std"] / l["mean"],
        "demand_change_pct": (l["mean"] - p) / p * 100,
    }).reset_index()

    # Upcoming window (next 7 days from tomorrow).
    up = _next7(ff)
    upa = up.assign(wadj=up["weather_calibrated"] - up["predicted"]).groupby("store_name").agg(
        mean_calibrated_next7=("calibrated", "mean"),
        weather_adj_next7_bowls=("wadj", "mean"),
        event_uplift_next7=("event_added_demand", "sum"),
        has_event_next7=("event_count", "max")).reset_index()
    upa["has_event_next7"] = (upa["has_event_next7"] > 0).astype(int)

    df = acc.merge(dem, on="store_name", how="left").merge(upa, on="store_name", how="left")
    df["demand_rank"] = df["mean_calibrated_next7"].rank(method="min", ascending=False).astype("Int64")
    for c in ("mae_bowls", "mape_pct", "bias_bowls", "bias_pct", "bias_slope",
              "mean_actual_last28", "demand_change_pct", "cv_actual_last28",
              "mean_calibrated_next7", "weather_adj_next7_bowls", "event_uplift_next7"):
        df[c] = df[c].round(2)
    write_table(root, "mart_store_scorecard", df)


# --------------------------------------------------------------------------- interval
def build_interval(root: Path) -> None:
    bt = _s10(root, "fact_backtest")
    ff = _s10(root, "fact_forecast")

    # Per-store de-biased residual dispersion e' = (actual − predicted) − median(...).
    q = {}
    for store, grp in bt.groupby("store_name"):
        e = (grp["actual_sales"] - grp["predicted"]).to_numpy()
        if len(e) < MIN_BACKTEST:
            continue
        ep = e - np.median(e)
        q[store] = (np.percentile(ep, 10), np.percentile(ep, SERVICE_LEVEL * 100),
                    np.percentile(ep, 90), len(e))
    # Global fallback for any store below the minimum sample.
    e_all = (bt["actual_sales"] - bt["predicted"]).to_numpy()
    ep_all = e_all - np.median(e_all)
    gq = (np.percentile(ep_all, 10), np.percentile(ep_all, SERVICE_LEVEL * 100),
          np.percentile(ep_all, 90), len(e_all))

    rows = []
    for _, r in ff.iterrows():
        q10, qsl, q90, n = q.get(r["store_name"], gq)
        cal = float(r["calibrated"])
        rows.append({
            "store_name": r["store_name"], "prefecture": r["prefecture"],
            "reference_date": r["reference_date"], "target_date": r["target_date"],
            "calibrated": round(cal, 1),
            "p10": round(max(0.0, cal + q10), 1), "p50": round(cal, 1),
            "p90": round(max(0.0, cal + q90), 1),
            "order_sl90": int(math.ceil(max(0.0, cal + qsl))), "n_backtest": int(n),
        })
    write_table(root, "mart_forecast_interval", pd.DataFrame(rows))


# --------------------------------------------------------------------------- anomaly
def build_anomaly(root: Path) -> None:
    fa = _s10(root, "fact_actuals").copy()
    bt = _s10(root, "fact_backtest")
    fa["d"] = pd.to_datetime(fa["date"])
    dmax = fa["d"].max()
    as_of = datetime.now(_JST).date().isoformat()
    pref = fa.groupby("store_name")["prefecture"].first()

    recent14 = fa[fa["d"] > dmax - pd.Timedelta(days=14)]
    prior28 = fa[(fa["d"] <= dmax - pd.Timedelta(days=14)) & (fa["d"] > dmax - pd.Timedelta(days=42))]
    r_stat = recent14.groupby("store_name")["actual_sales"].agg(["mean", "std"])
    p_stat = prior28.groupby("store_name")["actual_sales"].agg(["mean", "std"])

    rows = []

    def emit(store, sig, sev, val, detail):
        rows.append({"store_name": store, "prefecture": pref.get(store, ""),
                     "signal_type": sig, "severity": sev, "value": round(float(val), 2),
                     "detail": detail, "as_of": as_of})

    # demand_shift + volatility_spike (from actuals)
    for store in r_stat.index:
        rm, rs = r_stat.loc[store, "mean"], r_stat.loc[store, "std"]
        pm = p_stat["mean"].get(store, np.nan)
        ps = p_stat["std"].get(store, np.nan)
        if pm and not np.isnan(pm):
            chg = (rm - pm) / pm * 100
            if abs(chg) >= 30:
                emit(store, "demand_shift", "high", chg, f"直近14日が基準比 {chg:+.0f}%")
            elif abs(chg) >= 15:
                emit(store, "demand_shift", "med", chg, f"直近14日が基準比 {chg:+.0f}%")
        if ps and not np.isnan(ps) and ps > 0:
            cv_r, cv_p = rs / rm, ps / pm
            if cv_p > 0 and cv_r > 1.5 * cv_p:
                emit(store, "volatility_spike", "med", cv_r / cv_p, f"変動係数が基準の {cv_r/cv_p:.1f}倍")

    # persistent_bias + accuracy_degradation (from backtest)
    for store, grp in bt.groupby("store_name"):
        res = grp["residual"].to_numpy()
        pos, neg = np.mean(res > 0), np.mean(res < 0)
        share = max(pos, neg) * 100
        direction = "過大予測" if pos >= neg else "過小予測"
        if share >= 90:
            emit(store, "persistent_bias", "high", share, f"{share:.0f}%の日で{direction}")
        elif share >= 80:
            emit(store, "persistent_bias", "med", share, f"{share:.0f}%の日で{direction}")
        by_ref = (grp.assign(ape=grp["residual"].abs() / grp["actual_sales"])
                     .groupby("reference_date")["ape"].mean().sort_index() * 100)
        if len(by_ref) >= 3 and bool(np.all(np.diff(by_ref.to_numpy()) > 0)):
            emit(store, "accuracy_degradation", "med", by_ref.iloc[-1],
                 f"MAPEが直近{len(by_ref)}回で単調悪化 (最新 {by_ref.iloc[-1]:.0f}%)")

    df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=[c for c, _t, _n in __import__("_spec").TABLES["mart_anomaly"]["columns"]])
    df = df.sort_values(["severity", "signal_type", "store_name"], ascending=[False, True, True]) if rows else df
    write_table(root, "mart_anomaly", df)


def main() -> int:
    root = find_repo_root(Path(__file__).resolve())
    print(f"Building s10_analysis marts under {root / 'DATA/s10_analysis'}")
    build_scorecard(root)
    build_interval(root)
    build_anomaly(root)
    print("Marts done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
