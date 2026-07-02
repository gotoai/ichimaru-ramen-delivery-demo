---
name: retrieve-regional-geoshapes
description: >-
  Download Japanese regional (prefecture) boundary shapefiles from the e-stat
  GIS portal for the prefectures where Ichimaru operates. For each prefecture
  listed in docs/pipeline/profiles/Locations.md, looks it up by name in the portal's 地域
  list, downloads the 2020 Census small-area boundary ZIP, saves it to
  DATA/s01_raw/geoshape_<original_zip_filename>, and extracts it to
  DATA/s02_intermediate/geoshape_<NN>/ (NN = 2-digit prefecture code). Use when
  asked to retrieve, refresh, or bootstrap regional/prefecture map boundaries or
  polygons for the demo.
---

# Retrieve regional geo-shapes

Downloads prefecture-level **boundary shapefiles** (polygons) from the Japanese
government statistics GIS portal **e-stat** for the prefectures where Ichimaru
operates. These are the geographic counterpart to the population data produced by
the `retrieve-regional-population` skill.

## What it produces

For every prefecture referenced in `docs/pipeline/profiles/Locations.md`:

1. **Raw ZIP** in `DATA/s01_raw/`, named `geoshape_<original_zip_filename>` — for
   example `geoshape_A002005212020DDSWC13.zip` (Tokyo). This is the original
   e-stat download, left byte-for-byte unmodified.

2. **Extracted shapefile** in `DATA/s02_intermediate/geoshape_<NN>/`, where `NN`
   is the zero-padded 2-digit prefecture (JIS) code — e.g.
   `DATA/s02_intermediate/geoshape_13/` containing `r2ka13.shp`, `.shx`, `.dbf`,
   `.prj`.

Both the ZIP and the extracted contents are **overwritten** on each run.

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/retrieve-regional-geoshapes/scripts/retrieve_geoshapes.py
```

The script is dependency-free (Python standard library only). It prints one
`[OK]` / `[SKIP]` line per prefecture and exits non-zero if any prefecture could
not be retrieved.

Option: `--repo-root <path>` (auto-detected by default).

## What it does, step by step

1. **Read the target prefectures** from `docs/pipeline/profiles/Locations.md`. The file
   uses English names (Tokyo, Kanagawa, Chiba, Saitama); the script maps each to
   its Japanese name via a built-in 47-prefecture table.

2. **Find each prefecture in the portal's 地域 list.** The catalogue is fixed at
   the 2020 Census small-area boundaries (世界測地系緯度経度・Shape形式):
   - `serveyId` `A002005212020`, `toukeiCode` `00200521` (国勢調査), year `2020`
   - `aggregateUnitForBoundary=A` (小地域), `coordsys=1`, `datum=2000`, `format=shape`

   The script reads the portal's paginated download list (the JSON behind
   `…/gis/statmap-search/search_detail`) and parses the **地域** column rows
   (`NN 都道府県名`) to resolve each prefecture name to its 2-digit `prefCode`.

3. **Download and save the ZIP.** It downloads from the GIS file endpoint
   (`…/gis/statmap-search/data?dlserveyId=…&code=NN&…`), derives the original
   filename from the `Content-Disposition` header, and writes
   `DATA/s01_raw/geoshape_<filename>` (overwriting).

4. **Extract.** It unzips the boundary files into
   `DATA/s02_intermediate/geoshape_<NN>/` (overwriting), guarding against unsafe
   ZIP paths.

## Notes & maintenance

- Source: 統計GIS 国勢調査 2020年 小地域（境界データ）. Portal:
  <https://www.e-stat.go.jp/gis/statmap-search?type=2&toukeiCode=00200521&toukeiYear=2020&serveyId=A002005212020>
- CRS is geographic **JGD2000** (lon/lat, EPSG:4612). The `.dbf` is **Shift_JIS
  (CP932)** and carries the small-area join key `KEY_CODE` plus `PREF`+`CITY`
  (= 市区町村コード) and `S_AREA` (= 町丁字コード), so the polygons join to the
  `regional_population.tsv` produced by `retrieve-regional-population`. Note the
  boundary records exist only at the 町丁・字 level, so they do not line up 1:1
  with the hierarchy-level rows in that TSV.
- The catalogue ids and the `download_disp_flg=1` / `prefCode` parameters are the
  matching criteria; if e-stat changes its GIS endpoints or markup, the
  `LIST_URL` / `DOWNLOAD_URL` constants and the row regex in
  [scripts/retrieve_geoshapes.py](scripts/retrieve_geoshapes.py) are the parts to
  update.
- To target a different census year or boundary format, update the catalogue
  constants (`SERVEY_ID`, `COORD_SYS`, `DATUM`, `FORMAT`, `DOWNLOAD_TYPE`) at the
  top of the script.
