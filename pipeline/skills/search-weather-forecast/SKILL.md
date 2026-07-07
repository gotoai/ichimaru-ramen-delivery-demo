---
name: search-weather-forecast
description: >-
  Fetch the JMA short-range + weekly weather forecast for the prefectures where
  Ichimaru operates (docs/pipeline/profiles/Locations.md) and write a per-市区町村, per-day
  table for today+1/+2/+3, per docs/pipeline/search/Weather-forecast.md. Reads the JMA
  forecast JSON API (not the JavaScript web page), maps weather codes to the concise
  天気概況 telop names, estimates a daily rainfall volume, and writes
  DATA/s08_search/weather_forecast.tsv. Use to obtain live forecast weather.
---

# Fetch weather forecast

Downloads the JMA 府県天気予報 (short-range) + 週間天気予報 (weekly) for Ichimaru's
prefectures and produces a per-市区町村, per-day forecast table for the next three
days, following
[docs/pipeline/search/Weather-forecast.md](../../../docs/pipeline/search/Weather-forecast.md).

## What it produces

One UTF-8, tab-separated file with a header row, `DATA/s08_search/weather_forecast.tsv`
(directory created if missing) — one row per **(prefecture, sub_region, 市区町村,
target_date)** for `target_date` = today+1, today+2, today+3 (JST). Columns:

`prefecture`, `sub_region`, `shikuchoson`, `target_date` (YYYY-MM-DD), `天気概況`,
`降水確率(%)`, `信頼度`, `最高気温`, `最低気温`, `推定日降水量(mm)`.

## Data source

The `bosai/forecast/#area_code=...` links are a **JavaScript SPA** — the `#` fragment
never reaches the server and the rendered "７日先まで" table is not in the HTML, so it
cannot be scraped (and no headless browser is used). The skill reads the JSON the page
itself consumes:

- `forecast/data/forecast/<office>.json` — the forecast per prefecture office
  (東京 130000, 埼玉 110000, 千葉 120000, 神奈川 140000). A 2-element array: block[0]
  short-range (3 days), block[1] weekly (7 days).
- `common/const/area.json` — the area hierarchy; `class10s`→`class15s`→`class20s`
  gives each sub_region's 市区町村, and `offices` map prefecture names/enNames to
  office codes (target prefectures are read from `docs/pipeline/profiles/Locations.md`).
- `forecast/const/forecast_area.json` — each sub_region's representative temperature
  point (`amedas`).

## How to run it

From `pipeline/`, with the project `.venv` active (stdlib only — no venv strictly
required):

```bash
python skills/search-weather-forecast/scripts/search_weather_forecast.py
```

Options: `--repo-root <path>` (auto-detected), `--today YYYY-MM-DD` (override "today",
for testing). Re-running overwrites the output.

## How it works

- **天気概況** is the concise JMA telop name of each day's `weatherCode` (e.g.
  `200→曇`, `202→曇一時雨`, `313→雨後曇`) — the same vocabulary as the weather
  history. The 118-entry code→name table (`Forecast.Const.TELOPS`) is embedded in the
  script (there is no `const/telop.json`). Codes for today+1/+2 come from the
  short-range block, today+3 from the weekly block.
- **降水確率(%)** is the average of the short-range 6-hourly `pops` on that date
  (today+1), otherwise the weekly daily value (today+2/+3).
- **信頼度** comes from the weekly `reliabilities` (`A`/`B`/`C`); it is blank for the
  near days and present from today+3, matching the web table.
- **最高/最低気温** are the representative-point temperatures: today+1 from the
  short-range point (min/max of that date's values), today+2/+3 from the weekly
  `tempsMax`/`tempsMin` (central value; the `…Upper`/`…Lower` range is ignored). All
  市区町村 in a sub_region share the point's value.
- **推定日降水量(mm)** is estimated from `天気概況`: `大雨`/`暴風雨`→100; exactly `雨`
  (or `雨、…`/`…、雨`)→50; `一時雨`/`雨後`/`後雨`→15; any other `雨`→5; else 0.
- **Weekly is coarser than the short-range sub_regions.** Most prefectures publish a
  single prefecture-level weekly area and one weekly temperature point, so each
  sub_region resolves its today+3 fields to itself if present, else the sole
  prefecture-level weekly area/point.

## Notes & maintenance

- **Stdlib only** (HTTP + JSON). Requests retry transient network errors 3×.
- **Not reproducible:** it fetches live data, so output depends on the run time (JMA
  reissues forecasts ~05/11/17 JST). Unlike the deterministic pipeline skills.
- **Granularity:** `shikuchoson` uses JMA's finest forecast unit (`class20`), which is
  an individual 市区町村 for most areas but a grouped sub-area for 政令指定都市 (e.g.
  `横浜市北部`, `さいたま市南部`). Ward-level names (e.g. 横浜市鶴見区) are not JMA
  forecast units and do not appear.
- **Islands** (伊豆諸島/小笠原) have no demo stores; their today+3 fields may be blank
  because the weekly forecast merges/omits them. This is expected.
- The embedded telop table, endpoints, and rainfall thresholds are constants at the
  top of [scripts/search_weather_forecast.py](scripts/search_weather_forecast.py).
