---
name: retrieve-regional-population
description: >-
  Download Japanese regional (prefecture) population data from the e-stat portal
  for the prefectures where Ichimaru operates. For each prefecture listed in
  docs/profiles/Locations.md, fetches the 2020 Census small-area table
  "男女別人口，外国人人口及び世帯数－町丁・字等" (表番号 2) and saves the original CSV to
  DATA/s01_raw/population_<original_filename>. Use when asked to retrieve,
  refresh, or bootstrap regional/prefecture population data for the demo.
---

# Retrieve regional population

Downloads prefecture-level population CSVs from the Japanese government
statistics portal **e-stat** for the prefectures where Ichimaru operates.

## What it produces

For every prefecture referenced in `docs/profiles/Locations.md`:

1. **Raw CSV** in `DATA/s01_raw/`, named `population_<original_csv_filename>` —
   for example `population_h02_13.csv` (Tokyo). These are the original e-stat
   downloads (Shift_JIS encoded), left byte-for-byte unmodified.

2. **Combined extract** appended to
   `DATA/s02_intermediate/regional_population.tsv` — a single **UTF-8**,
   tab-separated file with the data rows of every prefecture, with these columns:

   ```
   市区町村コード  町丁字コード  地域階層レベル  都道府県名  市区町村名
   大字・町名  字・丁目名  総数  男  女  外国人人口  世帯数
   ```

   The combined TSV is **truncated and re-headed on the first append of a run**,
   then appended to for the remaining prefectures, so re-running the skill never
   duplicates rows. Numeric placeholders from the census (`X` = suppressed,
   `-` = none/zero) are carried over verbatim.

## How to run it

From the repo root, with the project `.venv` active:

```bash
source .venv/bin/activate
python ai/skills/retrieve-regional-population/scripts/retrieve_population.py
```

The script is dependency-free (Python standard library only) and idempotent —
re-running overwrites the files in place. It prints one `[OK]` / `[SKIP]` line
per prefecture and exits non-zero if any prefecture could not be retrieved.

Options: `--repo-root <path>` (auto-detected by default) and
`--out-dir <path>` (default `<repo-root>/DATA/s01_raw`).

## What it does, step by step

1. **Read the target prefectures** from `docs/profiles/Locations.md`. The file
   uses English names (Tokyo, Kanagawa, Chiba, Saitama); the script maps each to
   its Japanese name via a built-in 47-prefecture table. Adding a prefecture to
   Locations.md automatically includes it on the next run.

2. **Locate each prefecture on e-stat.** The catalogue is fixed at:
   - 統計 (toukei) `00200521` — 国勢調査 (Population Census)
   - 統計表 (tstat) `000001136464` — 令和2年国勢調査 (2020 Census)
   - tclass1 `000001136472` — 小地域集計 (small-area aggregation)

   The script fetches the listing page and reads the prefecture facet sidebar to
   resolve each prefecture name to its `tclass2` id, then opens that prefecture's
   file list.

3. **Pick the right table.** Within a prefecture's 30 files it selects the
   `<article>` whose **表番号 = `2`** and whose **統計表 =
   `男女別人口，外国人人口及び世帯数－町丁・字等`**, and reads that row's CSV
   `file-download` link (`fileKind=1`).

4. **Download and save.** It downloads the CSV and derives the original filename
   from the `Content-Disposition` header, saving it as
   `DATA/s01_raw/population_<original_filename>`.

5. **Extract and combine.** It decodes the CSV (CP932), keeps the data rows
   (those whose 市区町村コード is a 5-digit code), maps them to the 12 output
   columns above, and appends them to
   `DATA/s02_intermediate/regional_population.tsv` (truncating that file on the
   first append of the run).

## Notes & maintenance

- Source: 国勢調査 令和2年国勢調査 小地域集計 (e-stat). Listing page:
  <https://www.e-stat.go.jp/stat-search/files?toukei=00200521&tstat=000001136464&tclass1=000001136472>
- The script is polite (browser User-Agent, ~1s pause between requests).
  `/stat-search/` is permitted by e-stat's robots.txt.
- If e-stat changes its page markup, the regexes in
  [scripts/retrieve_population.py](scripts/retrieve_population.py) — `表番号`,
  the `js-data` table-name link, and the `file-download` href — are the parts to
  update. The catalogue ids above and the table name/number are the matching
  criteria and rarely change for a given census year.
- To target a different census year or table, update the catalogue ids,
  `TARGET_TABLE_NO`, and `TARGET_TABLE_NAME` constants at the top of the script.
