#!/usr/bin/env python3
"""Fetch the JMA short-range + weekly forecast for Ichimaru's prefectures.

Implements docs/calibration/Weather-forecast.md. For each target prefecture it reads
the JMA forecast JSON (NOT the JavaScript web page) and produces a per-市区町村,
per-day table for today+1, today+2, today+3 (JST):

  - Weather text (天気概況) is the concise telop name of each day's weather code
    (e.g. 200->曇, 202->曇一時雨, 313->雨後曇), the same vocabulary as the weather
    history; the code->name table (JMA Forecast.Const.TELOPS) is embedded below.
  - 降水確率, 信頼度, 最高/最低気温 are merged from the short-range block (block[0],
    today+1) and the weekly block (block[1], today+2/+3).
  - 推定日降水量(mm) is estimated from the weather text by a fixed rule.

Sub-region (一次細分区域) -> 市区町村 comes from common/const/area.json; the
sub-region's representative temperature point (amedas) from forecast/const/
forecast_area.json.

Source: https://www.jma.go.jp/bosai/forecast/data/forecast/<office>.json
Output: DATA/s08_calibration/weather_forecast.tsv (UTF-8 TSV, header row).

Stdlib only. NOTE: this fetches live data, so the result depends on the run time
(JMA reissues forecasts around 05/11/17 JST); it is not reproducible.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

JST = datetime.timezone(datetime.timedelta(hours=9))

ROOT = "https://www.jma.go.jp/bosai"
FORECAST_URL = ROOT + "/forecast/data/forecast/{office}.json"
AREA_URL = ROOT + "/common/const/area.json"
FORECAST_AREA_URL = ROOT + "/forecast/const/forecast_area.json"
USER_AGENT = "Mozilla/5.0 (compatible; ichimaru-demo/1.0; +fetch-weather-forecast skill)"

TARGET_OFFSETS = (1, 2, 3)  # today+1, today+2, today+3

OUT_COLS = [
    "prefecture", "sub_region", "shikuchoson", "target_date",
    "天気概況", "降水確率(%)", "信頼度", "最高気温", "最低気温", "推定日降水量(mm)",
]

# JMA weather code -> concise 天気概況 name (Forecast.Const.TELOPS, JA element).
WEATHER_CODE_TO_NAME = {
    "100": "晴", "101": "晴時々曇", "102": "晴一時雨", "103": "晴時々雨", "104": "晴一時雪", "105": "晴時々雪",
    "106": "晴一時雨か雪", "107": "晴時々雨か雪", "108": "晴一時雨か雷雨", "110": "晴後時々曇", "111": "晴後曇",
    "112": "晴後一時雨", "113": "晴後時々雨", "114": "晴後雨", "115": "晴後一時雪", "116": "晴後時々雪", "117": "晴後雪",
    "118": "晴後雨か雪", "119": "晴後雨か雷雨", "120": "晴朝夕一時雨", "121": "晴朝の内一時雨", "122": "晴夕方一時雨",
    "123": "晴山沿い雷雨", "124": "晴山沿い雪", "125": "晴午後は雷雨", "126": "晴昼頃から雨", "127": "晴夕方から雨",
    "128": "晴夜は雨", "130": "朝の内霧後晴", "131": "晴明け方霧", "132": "晴朝夕曇", "140": "晴時々雨で雷を伴う",
    "160": "晴一時雪か雨", "170": "晴時々雪か雨", "181": "晴後雪か雨", "200": "曇", "201": "曇時々晴", "202": "曇一時雨",
    "203": "曇時々雨", "204": "曇一時雪", "205": "曇時々雪", "206": "曇一時雨か雪", "207": "曇時々雨か雪",
    "208": "曇一時雨か雷雨", "209": "霧", "210": "曇後時々晴", "211": "曇後晴", "212": "曇後一時雨", "213": "曇後時々雨",
    "214": "曇後雨", "215": "曇後一時雪", "216": "曇後時々雪", "217": "曇後雪", "218": "曇後雨か雪",
    "219": "曇後雨か雷雨", "220": "曇朝夕一時雨", "221": "曇朝の内一時雨", "222": "曇夕方一時雨", "223": "曇日中時々晴",
    "224": "曇昼頃から雨", "225": "曇夕方から雨", "226": "曇夜は雨", "228": "曇昼頃から雪", "229": "曇夕方から雪",
    "230": "曇夜は雪", "231": "曇海上海岸は霧か霧雨", "240": "曇時々雨で雷を伴う", "250": "曇時々雪で雷を伴う",
    "260": "曇一時雪か雨", "270": "曇時々雪か雨", "281": "曇後雪か雨", "300": "雨", "301": "雨時々晴",
    "302": "雨時々止む", "303": "雨時々雪", "304": "雨か雪", "306": "大雨", "308": "雨で暴風を伴う", "309": "雨一時雪",
    "311": "雨後晴", "313": "雨後曇", "314": "雨後時々雪", "315": "雨後雪", "316": "雨か雪後晴", "317": "雨か雪後曇",
    "320": "朝の内雨後晴", "321": "朝の内雨後曇", "322": "雨朝晩一時雪", "323": "雨昼頃から晴", "324": "雨夕方から晴",
    "325": "雨夜は晴", "326": "雨夕方から雪", "327": "雨夜は雪", "328": "雨一時強く降る", "329": "雨一時みぞれ",
    "340": "雪か雨", "350": "雨で雷を伴う", "361": "雪か雨後晴", "371": "雪か雨後曇", "400": "雪", "401": "雪時々晴",
    "402": "雪時々止む", "403": "雪時々雨", "405": "大雪", "406": "風雪強い", "407": "暴風雪", "409": "雪一時雨",
    "411": "雪後晴", "413": "雪後曇", "414": "雪後雨", "420": "朝の内雪後晴", "421": "朝の内雪後曇",
    "422": "雪昼頃から雨", "423": "雪夕方から雨", "425": "雪一時強く降る", "426": "雪後みぞれ", "427": "雪一時みぞれ",
    "450": "雪で雷を伴う",
}

RETRYABLE = (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)


def http_get_json(url: str, attempts: int = 3):
    """GET a JSON document, retrying transient network errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except RETRYABLE as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise
            if attempt >= attempts:
                raise
            time.sleep(2 * attempt)


def estimate_rainfall(name: str):
    """Estimate a daily rainfall volume (mm) from the concise 天気概況, or '' if blank."""
    if not name:
        return ""
    if "大雨" in name or "暴風雨" in name:
        return 100
    if name == "雨" or name.startswith("雨、") or name.endswith("、雨"):
        return 50
    if "一時雨" in name or "雨後" in name or "後雨" in name:
        return 15
    if "雨" in name:
        return 5
    return 0


def jst_date(iso: str) -> datetime.date:
    """Date (JST) of an ISO timestamp like '2026-07-02T00:00:00+09:00'."""
    return datetime.datetime.fromisoformat(iso).astimezone(JST).date()


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "calibration" / "Weather-forecast.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/calibration/Weather-forecast.md not found).")


def read_target_offices(locations_md: Path, offices: dict) -> list[tuple[str, str]]:
    """Return [(prefecture_name, office_code)] referenced in Locations.md.

    Each JMA office carries a JA `name` (東京都) and `enName` (Tokyo); a prefecture is
    selected if either appears in Locations.md. Order follows Locations.md.
    """
    text = locations_md.read_text(encoding="utf-8")
    lower = text.lower()
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    # Rank matches by where they appear in the file so output order tracks the doc.
    hits = []
    for code, meta in offices.items():
        name = meta.get("name", "")
        en = meta.get("enName", "")
        pos = None
        if name and name in text:
            pos = text.index(name)
        if en and re.search(rf"\b{re.escape(en.lower())}\b", lower):
            p = lower.index(en.lower())
            pos = p if pos is None else min(pos, p)
        if pos is not None and code not in seen:
            hits.append((pos, name or en, code))
            seen.add(code)
    for _, name, code in sorted(hits):
        found.append((name, code))
    if not found:
        raise SystemExit(f"No known prefectures found in {locations_md}")
    return found


def municipalities(area: dict, class10_code: str) -> list[str]:
    """市区町村 names under a 一次細分区域 (class10 -> class15 -> class20)."""
    names: list[str] = []
    for c15 in area["class10s"].get(class10_code, {}).get("children", []):
        for c20 in area["class15s"].get(c15, {}).get("children", []):
            nm = area["class20s"].get(c20, {}).get("name")
            if nm:
                names.append(nm)
    return names


def index_forecast(data: list):
    """Index one prefecture's forecast JSON into per-(sub_region/point, date) lookups."""
    b0, b1 = data[0], data[1]

    # Short-range weather codes: sub_region code -> {date: weatherCode}
    short_code: dict[str, dict] = {}
    ts0 = b0["timeSeries"][0]
    for a in ts0["areas"]:
        short_code[a["area"]["code"]] = {
            jst_date(td): wc for td, wc in zip(ts0["timeDefines"], a["weatherCodes"])
        }
    # Short-range pops (6-hourly): sub_region code -> {date: [pop, ...]}
    short_pop: dict[str, dict] = {}
    tsp = b0["timeSeries"][1]
    for a in tsp["areas"]:
        byd: dict = {}
        for td, p in zip(tsp["timeDefines"], a["pops"]):
            byd.setdefault(jst_date(td), []).append(p)
        short_pop[a["area"]["code"]] = byd
    # Short-range temps: point code -> {date: [temp, ...]}
    short_temp: dict[str, dict] = {}
    tst = b0["timeSeries"][2]
    for a in tst["areas"]:
        byd: dict = {}
        for td, t in zip(tst["timeDefines"], a["temps"]):
            if t != "":
                byd.setdefault(jst_date(td), []).append(float(t))
        short_temp[a["area"]["code"]] = byd

    # Weekly weather/pop/reliability: sub_region code -> {date: {...}}
    week: dict[str, dict] = {}
    wk0 = b1["timeSeries"][0]
    for a in wk0["areas"]:
        m: dict = {}
        for i, td in enumerate(wk0["timeDefines"]):
            m[jst_date(td)] = {
                "code": a["weatherCodes"][i], "pop": a["pops"][i],
                "reli": a["reliabilities"][i],
            }
        week[a["area"]["code"]] = m
    # Weekly temps: point code -> {date: {min, max}}
    week_temp: dict[str, dict] = {}
    wkt = b1["timeSeries"][1]
    for a in wkt["areas"]:
        m: dict = {}
        for i, td in enumerate(wkt["timeDefines"]):
            m[jst_date(td)] = {"min": a["tempsMin"][i], "max": a["tempsMax"][i]}
        week_temp[a["area"]["code"]] = m

    sub_regions = [(a["area"]["code"], a["area"]["name"]) for a in ts0["areas"]]
    return sub_regions, short_code, short_pop, short_temp, week, week_temp


def avg_pop(values: list) -> str:
    nums = [int(v) for v in values if v != ""]
    return str(round(sum(nums) / len(nums))) if nums else ""


def fmt_temp(v) -> str:
    return str(int(round(float(v)))) if v not in ("", None) else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD, JST)")
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    today = (datetime.date.fromisoformat(args.today) if args.today
             else datetime.datetime.now(JST).date())
    targets = [today + datetime.timedelta(days=n) for n in TARGET_OFFSETS]

    area = http_get_json(AREA_URL)
    fcarea = http_get_json(FORECAST_AREA_URL)
    # sub_region (class10) -> representative temperature point (amedas)
    sub_to_point: dict[str, str] = {}
    for entries in fcarea.values():
        for e in entries:
            pts = e.get("amedas") or []
            if pts:
                sub_to_point[e["class10"]] = pts[0]

    offices = read_target_offices(repo_root / "docs" / "profiles" / "Locations.md",
                                  area["offices"])
    print(f"Today (JST): {today}  ->  targets: {', '.join(map(str, targets))}")
    print("Prefectures: " + "、".join(name for name, _ in offices))

    rows: list[list] = []
    for pref_name, office in offices:
        data = http_get_json(FORECAST_URL.format(office=office))
        sub_regions, short_code, short_pop, short_temp, week, week_temp = index_forecast(data)
        # The weekly block is coarser than the short-range sub_regions: most
        # prefectures publish a single prefecture-level weekly area (and one weekly
        # temperature point). Resolve each sub_region to its weekly area/point —
        # itself if present, else the sole prefecture-level one.
        only_week = next(iter(week)) if len(week) == 1 else None
        only_wtemp = next(iter(week_temp)) if len(week_temp) == 1 else None
        for sub_code, sub_name in sub_regions:
            towns = municipalities(area, sub_code)
            if not towns:
                continue  # e.g. sub_regions with no class20 (skip; no stores there)
            point = sub_to_point.get(sub_code, "")
            wk_area = sub_code if sub_code in week else only_week
            wk_point = point if point in week_temp else only_wtemp
            for d in targets:
                # 天気概況: short-range code for near days, weekly code for later days.
                code = short_code.get(sub_code, {}).get(d)
                if not code and wk_area:
                    code = week[wk_area].get(d, {}).get("code")
                name = WEATHER_CODE_TO_NAME.get(code or "", "")
                # 降水確率: average short-range 6-hourly slots, else weekly daily value.
                if d in short_pop.get(sub_code, {}):
                    pop = avg_pop(short_pop[sub_code][d])
                elif wk_area:
                    pop = week[wk_area].get(d, {}).get("pop", "")
                else:
                    pop = ""
                reli = week[wk_area].get(d, {}).get("reli", "") if wk_area else ""
                # 気温: weekly point temps when present, else short-range min/max.
                wt = week_temp.get(wk_point, {}).get(d, {}) if wk_point else {}
                if wt.get("max", "") != "":
                    hi, lo = fmt_temp(wt.get("max")), fmt_temp(wt.get("min"))
                else:
                    vals = short_temp.get(point, {}).get(d, [])
                    hi = fmt_temp(max(vals)) if vals else ""
                    lo = fmt_temp(min(vals)) if vals else ""
                rain = estimate_rainfall(name)
                for town in towns:
                    rows.append([pref_name, sub_name, town, d.isoformat(),
                                 name, pop, reli, hi, lo, rain])

    out = repo_root / "DATA" / "s08_calibration" / "weather_forecast.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(OUT_COLS)
        w.writerows(rows)
    ntowns = len({(r[0], r[2]) for r in rows})
    print(f"Wrote {out} ({len(rows)} rows; {ntowns} 市区町村 × {len(targets)} days).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
