# Ichimaru Ramen Delivery Demo

A demo AI-agent application for Ichimaru ramen's food-delivery planning system.

## Demo setup

The demo runs on a few prepared datasets. Run the steps below once after
cloning the repository.

### Prerequisites

- Python 3.12 (3.12.10 is used during development)
- The project virtual environment:

  ```bash
  python3.12 -m venv .venv
  source .venv/bin/activate
  ```

Run `make` (or `make help`) at any time to see the available tasks.

### Step 1 — Download base data

Downloads the 2020 Census data for the prefectures where Ichimaru operates
(defined in [docs/profiles/Locations.md](docs/profiles/Locations.md)):

```bash
make base-data
```

This runs four skills in sequence:

- **`retrieve-regional-population`** — prefecture population CSVs into
  `DATA/s01_raw/`, combined into the UTF-8 TSV
  `DATA/s02_intermediate/regional_population.tsv`. See
  [ai/skills/retrieve-regional-population/SKILL.md](ai/skills/retrieve-regional-population/SKILL.md).
- **`retrieve-regional-geoshapes`** — prefecture boundary shapefiles (polygons)
  into `DATA/s01_raw/`, extracted into `DATA/s02_intermediate/geoshape_<NN>/`
  (NN = 2-digit prefecture code). See
  [ai/skills/retrieve-regional-geoshapes/SKILL.md](ai/skills/retrieve-regional-geoshapes/SKILL.md).
- **`retrieve-weather-station`** — JMA AMeDAS station master, reduced to active
  stations (End Date = 9999-99-99) as a UTF-8 TSV
  `DATA/s02_intermediate/weather-station.tsv`. See
  [ai/skills/retrieve-weather-station/SKILL.md](ai/skills/retrieve-weather-station/SKILL.md).
- **`retrieve-weather-history`** — JMA daily weather history (the last 3 full
  calendar years, computed from the system date; all prefectures combined into
  one CSV per month) into `DATA/s01_raw/`, reshaped
  into long-format UTF-8 TSVs under `DATA/s02_intermediate/`. This is the **long
  step** (41 monthly downloads, ~4–6 min, with a courtesy pause between
  downloads). See
  [ai/skills/retrieve-weather-history/SKILL.md](ai/skills/retrieve-weather-history/SKILL.md).

> Working inside Claude Code? You can instead ask the agent to **"download the
> base regional data"** — the skills trigger the same scripts.
