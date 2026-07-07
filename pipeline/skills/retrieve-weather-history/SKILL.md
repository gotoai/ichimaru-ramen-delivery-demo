---
name: retrieve-weather-history
description: >-
  Download daily weather history from the JMA (Japan Meteorological Agency)
  "過去の気象データ・ダウンロード" portal for the prefectures where Ichimaru
  operates (docs/pipeline/profiles/Locations.md). Downloads daily values for all
  observation stations, all prefectures combined, one calendar month at a time
  from January three years ago up to the month two days before today (at least
  three full calendar years, computed from the system date), saving the raw CSV to
  DATA/s01_raw/ and a long-format UTF-8 TSV to DATA/s02_intermediate/.
  Use when asked to retrieve, refresh, or bootstrap historical weather data for
  the demo.
---

# Retrieve weather history

Downloads daily-value (日別値) weather observation CSVs from the Japanese
Meteorological Agency download portal (obsdl) for the prefectures where Ichimaru
operates.

## What it produces

By default the skill downloads **all stations of all target prefectures in a
single request per month** (the JMA per-download data limit comfortably allows
this for the 4 demo prefectures), producing per month:

1. **Raw CSV** (wide format) in `DATA/s01_raw/`, named
   `weather-history-all-<YYYY-MM-01>.csv`. This is the original JMA download
   (Shift_JIS / CP932), saved unmodified, with one column-block per station.

2. **Long-format TSV** in `DATA/s02_intermediate/`, named
   `weather_history_all_<YYYY-MM-01>.tsv` — **UTF-8**, tab-separated, one row per
   (station, date), with prefectures distinguished by the `都道府県` column. The
   36 columns are:

   `都道府県  観測地点  日付  曜日` followed by, for each element, its value and
   `_品質情報` / `_均質番号` sub-columns (and `最大風速(m/s)_風向` /
   `…_風向_品質情報`). The `現象なし情報` sub-columns present in the raw CSV for
   降水量/降雪量 are dropped. Elements a station does not observe (e.g. AMeDAS
   sites lack 雲量/天気概況) are left blank.

`<YYYY-MM-01>` is the first day of the downloaded month.

**Fallback.** If the combined request is ever rejected (e.g. too many stations
for the per-download limit), the skill automatically falls back to one download
**per prefecture**, writing `weather-history-<prefecture>-<YYYY-MM-01>.csv` and
`weather_history_<prefecture>_<YYYY-MM-01>.tsv` instead (`<prefecture>` = the JMA
area name: 東京, 神奈川, 千葉, 埼玉). Pass `--per-prefecture` to force this mode.

Elements retrieved (all observation stations): 日平均/最高/最低気温,
降水量の日合計, 降雪量の日合計, 日平均風速, 日最大風速, 日平均相対湿度,
日平均雲量, 天気概況（昼）.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/retrieve-weather-history/scripts/retrieve_weather.py
```

The script is dependency-free (Python standard library only). By default it
covers the **at least last 3 full calendar years** — end month = The date of 2 days before current system date, start month = January three years before that — computed from the system
date (e.g. run in 2026-06-15 → 2023-01 .. 2026-06 = 42 monthly combined downloads, with the last month having 13 days),
taking roughly **12–30 minutes** in total. Each monthly download is generated
server-side by the JMA portal and takes about **15–40 seconds** on its own
(proportional to stations × days; ~76 stations × 10 elements × ~30 days), varying
with JMA server load — that, plus a courtesy pause between downloads (see below),
dominates the runtime. The per-prefecture fallback is 4× as many downloads (each
faster, since it covers fewer stations).

Progress is shown with a single refreshable status line — a progress bar, the
elapsed seconds and projected total runtime (`Total`), the count, and the month
currently downloading — that redraws in place on a terminal (and falls back to one
plain line per completed download when the output is piped or logged), e.g.:

```
Weather history |==============--------------|  50%  (450 sec / Total 900 sec)  18/36  2024-12-01
```

The `Total` is the running time stretched to 100% by the completed fraction (i.e.
`elapsed / progress`, the projected total runtime), shown as `--` until the first
download completes.

Useful options for testing or partial refreshes:

```bash
# one prefecture, two months
python .../retrieve_weather.py --prefectures "東京都" --start 2023-01 --end 2023-02
```

- `--per-prefecture` — force one download per prefecture (skip the combined
  attempt).

- `--prefectures "東京都,千葉県"` — override the Locations.md list (full names).
- `--start YYYY-MM`, `--end YYYY-MM` — override the auto-computed period.
- `--repo-root <path>` — auto-detected by default.

Re-running overwrites existing files.

## How it works

The portal is a stateful POST API (no headless browser needed):

1. **Read target prefectures** from `docs/pipeline/profiles/Locations.md` (English names
   mapped to Japanese via a built-in 47-prefecture table).
2. **Open a session** with a GET to `obsdl/index.php` (an in-memory cookie jar
   keeps the `ci_session` cookie).
3. **Resolve each prefecture** to a JMA area code (`pd`) by reading the portal's
   地点を選ぶ map (`top/station` with `pd=00`).
4. **List all stations** for each prefecture (`top/station` with the `pd`),
   keeping every station whose `prid` matches, and combine them into one list.
5. **Download each month** by POSTing to `show/table` with `downloadFlag=true`,
   `aggrgPeriod=1` (日別値), the fixed element list, the display-option flags, and
   `ymdList=[startYear,endYear,startMonth,endMonth,startDay,endDay]` **as
   strings** (integers are rejected by the server — this is the key detail). All
   prefectures' stations go in a single request (`stationNumList`); if that is
   rejected the skill retries per prefecture.
6. **Reshape wide → long** and write the UTF-8 TSV. The wide CSV's header rows
   (都道府県 / 観測地点 / element / 風向 / 品質情報·均質番号) are parsed to split
   the columns into per-station blocks and map each to the fixed 36-column long
   schema; then one row is emitted per (station, date).

## Notes & maintenance

- **Courtesy / rate limiting.** The JMA portal explicitly asks users to refrain
  from excessive automated access. The script downloads one month at a time and
  pauses `WAIT_BETWEEN_DOWNLOAD_ONCE = 2` seconds between downloads. Please keep
  this in place and avoid running the full job repeatedly.
- **Network retry.** The portal occasionally times out or drops a connection
  mid-download. Every HTTP request (session GET, station listing, and each month
  download) is retried up to `NET_ATTEMPTS = 3` times on transient errors
  (timeouts, dropped/refused connections, `IncompleteRead`), pausing
  `RETRY_BACKOFF × attempt` seconds between tries; retry warnings go to stderr.
  If all attempts fail the error is re-raised. This is separate from the
  session-expiry retry (a rejected CSV download is retried once after refreshing
  the session). Non-transient HTTP errors (4xx) are not retried.
- The portal limits the data volume per download, which is why the script works
  month-by-month rather than fetching the whole range at once.
- Some elements (雲量, 天気概況, 相対湿度) are only observed at staffed stations
  (`s…`), so AMeDAS (`a…`) station columns for those elements are blank — this is
  expected.
- The fixed request configuration (endpoints, element codes, option flags,
  period, and the string `ymdList` format) lives in constants at the top of
  [scripts/retrieve_weather.py](scripts/retrieve_weather.py); update there if the
  portal changes or different elements/periods are needed.
