# AGENTS Instructions

This repository is a monorepo that contains a demo AI agent application of Ichimaru-ramen food delivery planning system.

# Python environment

Python is needed to build and run the demo system. After installation, run `source ichimaru-ramen-delivery-demo/.venv/bin/activate` to activate the Python environment. During development and test of this deom Python 3.12.10 is used.

# Skills

Project agent skills live under `ai/skills/<skill-name>/` (each with a `SKILL.md`). This is a custom location and is not auto-discovered — read the relevant `SKILL.md` before running a skill.

- `retrieve-regional-population` — download 2020 Census prefecture population CSVs into `DATA/s01_raw/`, then extract and combine their rows into the UTF-8 TSV `DATA/s02_intermediate/regional_population.tsv`.
- `retrieve-regional-geoshapes` — download 2020 Census prefecture boundary shapefiles (ZIP) into `DATA/s01_raw/`, then extract each into `DATA/s02_intermediate/geoshape_<NN>/` (NN = 2-digit prefecture code).
- `retrieve-weather-station` — download the JMA AMeDAS station master (`amdmaster.index4`) into `DATA/s01_raw/weather-station.csv`, keep only active stations (End Date = 9999-99-99), and write a reduced UTF-8 TSV `DATA/s02_intermediate/weather-station.tsv`.
- `retrieve-weather-history` — download JMA daily weather history (from January three years ago up to the month two days before today — at least three full calendar years, computed from the system date, with the final month truncated to that day; all prefectures combined, one request per month) into `DATA/s01_raw/weather-history-all-<YYYY-MM-01>.csv`, then reshape each to a long-format UTF-8 TSV `DATA/s02_intermediate/weather_history_all_<YYYY-MM-01>.tsv` (都道府県 column distinguishes prefectures; falls back to per-prefecture files if the combined request is rejected). This is the long step (~4-6 min).

`make base-data` runs all four skills above in sequence (the weather-history step is long, ~4-6 min).

- `synthesize-stores` — population-weighted sampling of Ichimaru's store network. For each prefecture and store count in `docs/profiles/Locations.md`, samples that many distinct 大字・町 (ooaza) with probability proportional to 2020 Census population, places a store at each ooaza's polygon centroid (from the geoshape shapefiles), names it `<都道府県名><市区町村名><大字・町名>店`, draws weekday/weekend sales baselines, and writes the UTF-8 TSV `DATA/s03_primary/store.tsv`. Reproducible via `synthetics/random_seed` in `config/config.yaml`. Depends on the `retrieve-regional-population` and `retrieve-regional-geoshapes` outputs.
- `synthesize-pois` — same population-weighted ooaza sampling as `synthesize-stores`, but for POIs, placing each at a **uniform random point inside the sampled ooaza polygon** (not the centroid) and sampling **with replacement** (an ooaza may recur). Driven by `config/config.yaml` (`synthetics/competitors/*`, `synthetics/home_buildings/*`, `time_horizon`), it writes `DATA/s03_primary/competitor.tsv` (competitor stores with weekday/weekend baselines and `open_date`/`end_date`, where `end_date = open_date + business_duration` or `9999-99-99` if still open) and `DATA/s03_primary/home_building.tsv` (residences with a `unit` count, `open_date`, and a permanent `9999-99-99` `end_date`). Names get a `_<seq>` suffix to stay unique. Same intermediate-data dependencies as `synthesize-stores`.
- `synthesize-events` — same population-weighted ooaza sampling as `synthesize-pois` (random point inside the ooaza polygon, with replacement, `_<seq>`-suffixed names), for people-gathering events. Driven by `config/config.yaml` (`synthetics/events/{numbers,people_range}`, `time_horizon/sales_history`), it writes `DATA/s03_primary/event.tsv` (`prefecture`, `event_name`, `latitude`, `longitude`, `people`, `event_date`). `event_date` is a weighted random date over the `sales_history` window with April/August/October days weighted 5×. Same intermediate-data dependencies as `synthesize-stores`.

# Python dependencies

Skills that need third-party libraries declare them in `requirements.txt` (kept lean). Install with `pip install -r requirements.txt` inside the activated `.venv`. Currently: `pyshp` and `PyYAML` (used by `synthesize-stores`).
