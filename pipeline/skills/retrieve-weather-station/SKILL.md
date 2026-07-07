---
name: retrieve-weather-station
description: >-
  Download the JMA AMeDAS station master (amdmaster.index4), keep only currently
  active stations (End Date = 9999-99-99), and write a reduced UTF-8 TSV of
  station metadata (number, kanji name, lat/lon/altitude, anemometer height,
  observed-element flags, start/end dates) to
  DATA/s02_intermediate/weather-station.tsv. Use when asked to retrieve, refresh,
  or bootstrap the weather observation station list/metadata for the demo.
---

# Retrieve weather station master

Downloads the Japan Meteorological Agency **AMeDAS station-history master**
(全国の地域気象観測所の履歴情報) and reduces it to a tidy list of currently active
stations with their location and capability metadata.

## What it produces

1. **Raw CSV** in `DATA/s01_raw/weather-station.csv` — the original
   `amdmaster.index4` download (Shift_JIS / CP932), saved unmodified.
2. **Active-station TSV** in `DATA/s02_intermediate/weather-station.tsv` —
   **UTF-8**, tab-separated, one row per currently active station, with 14
   columns:

   `Station Number  Station Name (Kanji)  Latitude_Precipitation
   Longitude_Precipitation  Altitude_Precipitation  Height of Anemometer
   Precipitation  Wind Speed  Temperature  Sunshine Duration
   Depth of Snow Cover  Humidity  Start Date  End Date`

   The `Precipitation … Humidity` columns are 0/1 flags for whether the station
   observes that element (`Sunshine Duration` may also be `2` = derived from
   satellite data, per the spec).

## How to run it

From `pipeline/`, with the project `.venv` active:

```bash
source .venv/bin/activate
python skills/retrieve-weather-station/scripts/retrieve_weather_station.py
```

The script is dependency-free (Python standard library only) and idempotent —
re-running overwrites both files. Option: `--repo-root <path>` (auto-detected by
default). Also runnable via `make base-data` (this skill is one of its steps).

## How it works

1. **Download** `amdmaster.index4` from the JMA stats site.
2. **Filter** to active stations: per the format spec, the currently-valid
   record for a station is the one whose 観測終了年月日 / **End Date is
   `9999-99-99`**; all historical rows are dropped.
3. **Reduce & reshape**: select the 14 columns above from the 33-field source
   record, stripping field padding (including the full-width spaces used to pad
   the kanji name), and write the UTF-8 TSV.

## Notes & maintenance

- Source CSV:
  <https://www.data.jma.go.jp/stats/data/mdrr/chiten/meta/amdmaster.index4>
- Format spec (PDF):
  <https://www.data.jma.go.jp/stats/data/mdrr/man/amdmasterindex4_format.pdf>
- The source has two header rows and is **CP932**; the kanji station name is
  full-width-space padded (Python's `str.strip()` removes that).
- The output column → source-column-index mapping is the `OUTPUT_COLUMNS`
  constant at the top of
  [scripts/retrieve_weather_station.py](scripts/retrieve_weather_station.py);
  update there if the source layout changes or different fields are needed.
