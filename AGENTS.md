# AGENTS Instructions

This repository is a monorepo that contains a demo AI agent application of Ichimaru-ramen food delivery planning system.

# Python environment

Python is needed to build and run the demo system. After installation, run `source ichimaru-ramen-delivery-demo/.venv/bin/activate` to activate the Python environment. During development and test of this deom Python 3.12.10 is used.

# Skills

Project agent skills live under `ai/skills/<skill-name>/` (each with a `SKILL.md`). This is a custom location and is not auto-discovered — read the relevant `SKILL.md` before running a skill.

- `retrieve-regional-population` — download 2020 Census prefecture population CSVs into `DATA/s01_raw/`, then extract and combine their rows into the UTF-8 TSV `DATA/s02_intermediate/regional_population.tsv`.
- `retrieve-regional-geoshapes` — download 2020 Census prefecture boundary shapefiles (ZIP) into `DATA/s01_raw/`, then extract each into `DATA/s02_intermediate/geoshape_<NN>/` (NN = 2-digit prefecture code).
- `retrieve-weather-station` — download the JMA AMeDAS station master (`amdmaster.index4`) into `DATA/s01_raw/weather-station.csv`, keep only active stations (End Date = 9999-99-99), and write a reduced UTF-8 TSV `DATA/s02_intermediate/weather-station.tsv`.
- `retrieve-weather-history` — download JMA daily weather history (last 3 full calendar years, i.e. Jan of three-years-ago .. Dec of last year, computed from the system date; all prefectures combined, one request per month) into `DATA/s01_raw/weather-history-all-<YYYY-MM-01>.csv`, then reshape each to a long-format UTF-8 TSV `DATA/s02_intermediate/weather_history_all_<YYYY-MM-01>.tsv` (都道府県 column distinguishes prefectures; falls back to per-prefecture files if the combined request is rejected). This is the long step (~4-6 min).

`make base-data` runs all four skills above in sequence (the weather-history step is long, ~4-6 min).
