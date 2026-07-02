## Weather Forecast

Fetch the JMA short-range + weekly forecast for the prefectures where Ichimaru
operates and produce a per-市区町村, per-day table for the next three days.

### Data source (JSON API — not the web page)

The `https://www.jma.go.jp/bosai/forecast/#area_code=...` links are a **JavaScript
single-page app**: the URL `#` fragment is client-side only, and the rendered
"７日先まで" table does not exist in the fetched HTML. Do **not** scrape those pages
(and no headless browser). Instead read the JSON the page itself consumes, one file
per prefecture office code:

```
https://www.jma.go.jp/bosai/forecast/data/forecast/<office>.json
```

| Prefecture | office | forecast JSON |
|---|---|---|
| 東京都 | 130000 | forecast/data/forecast/130000.json |
| 埼玉県 | 110000 | forecast/data/forecast/110000.json |
| 千葉県 | 120000 | forecast/data/forecast/120000.json |
| 神奈川県 | 140000 | forecast/data/forecast/140000.json |

Each file is a 2-element array:

- **block[0] — short-range (3 days).** `timeSeries[0]`: `weatherCodes`, `weathers`,
  per **一次細分区域** (sub_region, e.g. `東京地方`). `timeSeries[1]`: `pops`
  (降水確率) in 6-hour steps. `timeSeries[2]`: `temps` per representative point.
- **block[1] — weekly (7 days).** `timeSeries[0]`: `weatherCodes`, `pops` (daily),
  `reliabilities` (信頼度). `timeSeries[1]`: `tempsMin`/`tempsMax` (+ Upper/Lower) per
  point.

The three target dates — **today+1, today+2, today+3** (JST) — straddle both blocks,
so the two are merged per date (this is exactly what the web table shows).

### Sub region (細分地域) → 市区町村

Use the machine-readable hierarchy `https://www.jma.go.jp/bosai/common/const/area.json`
(not `shichoson_ichiran.html`). Join the forecast area **code** through
`class10s` (一次細分区域 = sub_region) → `class15s` → `class20s` (市区町村 =
shikuchoson). Every 市区町村 under a sub_region inherits that sub_region's forecast.
(Island sub_regions such as 伊豆諸島/小笠原 have no demo stores; include or drop them
per need.)

### 天気概況 from the weather code

For **every** target date, `天気概況` is the JMA telop name for that day's
`weatherCode`, i.e. the concise kanji form shown in the 7-day table (`曇`, `曇一時雨`,
`雨後曇`, `晴時々曇`, …) — the same vocabulary as the weather history. The mapping is
JMA's `Forecast.Const.TELOPS` table (118 codes; the Japanese name is element index 3,
e.g. `200→曇`, `202→曇一時雨`, `313→雨後曇`). It is **inlined in the forecast page's
JavaScript** (there is no `const/telop.json`); embed the code→name table as a
constant in the script. Take the code for today+1/+2 from block[0]`timeSeries[0]`, and
for today+3 from block[1] weekly `weatherCodes`.

### Per-date field extraction (merge block[0] + block[1])

For each sub_region and each target date:

- `天気概況`: `TELOPS[weatherCode][3]` (as above).
- `降水確率(%)`: today+1/+2 — the average of block[0] `pops` whose `timeDefines`
  fall on that JST date (the 6-hour slots); today+3 — the weekly daily `pops` value.
  Blank if unavailable.
- `信頼度`: from block[1] weekly `reliabilities`, aligned by date (string `A`/`B`/`C`).
  Typically blank for today+1/+2 and present from today+3.
- `最高気温` / `最低気温`: today+1 from block[0] `temps` (representative point);
  today+2/+3 from block[1] weekly `tempsMax`/`tempsMin`. Take the single central
  value, ignoring the `…Upper`/`…Lower` range. Temperatures are per representative
  point, so all 市区町村 in a sub_region share the point's value.

### Estimate the rainfall volume

From the (concise) `天気概況`, estimate a daily rainfall volume:

- if the name contains `大雨` or `暴風雨` → **100 mm**.
- else if the name is exactly `雨`, or starts with `雨、`, or ends with `、雨` → **50 mm**.
- else if the name contains `一時雨`, or `雨後`, or `後雨` → **15 mm**.
- else if the name contains `雨` → **5 mm**.
- else → **0 mm**.

Record as `推定日降水量(mm)`.

### Output

One row per (prefecture, sub_region, 市区町村, target_date). Columns:

  - prefecture
  - sub_region
  - shikuchoson (市区町村)
  - target_date (YYYY-MM-DD)
  - 天気概況
  - 降水確率(%)
  - 信頼度
  - 最高気温
  - 最低気温
  - 推定日降水量(mm)

Save to `DATA/s08_search/weather_forecast.tsv` (UTF-8 TSV, header row; create
the directory if missing).

### Notes

  - Stdlib-only (HTTP + JSON parsing); **not reproducible** — it fetches live data and
    the result depends on the run time (JMA reissues forecasts ~05/11/17 JST).
  - Read target prefectures from `docs/pipeline/profiles/Locations.md`, consistent with the
    other retrieval skills.
