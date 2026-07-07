# Ichimaru Ramen Delivery Demo

A demo AI-agent application for Ichimaru ramen's food-delivery planning system.

## Demo setup

The demo runs on a few prepared datasets. Run the steps below once after
cloning the repository.

### Prerequisites

- Python 3.12 (3.12.10 is used during development)
- The project virtual environment, created under `pipeline/`. Run all pipeline
  commands from the `pipeline/` directory:

  ```bash
  cd pipeline
  python3.12 -m venv .venv
  source .venv/bin/activate
  ```

Run `make` (or `make help`) from `pipeline/` at any time to see the available tasks.

### Step 1 — Download base data

Downloads the 2020 Census data for the prefectures where Ichimaru operates
(defined in [docs/pipeline/profiles/Locations.md](../docs/pipeline/profiles/Locations.md)):

```bash
make base-data
```

This runs four skills in sequence:

- **`retrieve-regional-population`** — prefecture population CSVs into
  `DATA/s01_raw/`, combined into the UTF-8 TSV
  `DATA/s02_intermediate/regional_population.tsv`. See
  [pipeline/skills/retrieve-regional-population/SKILL.md](skills/retrieve-regional-population/SKILL.md).
- **`retrieve-regional-geoshapes`** — prefecture boundary shapefiles (polygons)
  into `DATA/s01_raw/`, extracted into `DATA/s02_intermediate/geoshape_<NN>/`
  (NN = 2-digit prefecture code). See
  [pipeline/skills/retrieve-regional-geoshapes/SKILL.md](skills/retrieve-regional-geoshapes/SKILL.md).
- **`retrieve-weather-station`** — JMA AMeDAS station master, reduced to active
  stations (End Date = 9999-99-99) as a UTF-8 TSV
  `DATA/s02_intermediate/weather-station.tsv`. See
  [pipeline/skills/retrieve-weather-station/SKILL.md](skills/retrieve-weather-station/SKILL.md).
- **`retrieve-weather-history`** — JMA daily weather history from January of the
  year three years before the end year up to the month of the day two days before
  today JST (the final month truncated to that day), computed from the system
  date; all prefectures combined into one CSV per month under `DATA/s01_raw/`,
  reshaped into long-format UTF-8 TSVs under `DATA/s02_intermediate/`. This is the
  **long step** (~3 years of monthly downloads, ~4–6 min, with a courtesy pause
  between downloads). See
  [pipeline/skills/retrieve-weather-history/SKILL.md](skills/retrieve-weather-history/SKILL.md).

> Working inside Claude Code? You can instead ask the agent to **"download the
> base regional data"** — the skills trigger the same scripts.

### Step 2 — Synthesize primary data

Generates the demo's synthetic business data from the base data above (driven by
[pipeline/config/config.yaml](config/config.yaml), reproducible via its `random_seed`):

```bash
make synthetics
```

Run this **after `make base-data`** — the synthesis skills read the
`s02_intermediate` outputs. It runs four skills in sequence, writing to
`DATA/s03_primary/`:

- **`synthesize-stores`** — population-weighted store network → `store.tsv`. See
  [pipeline/skills/synthesize-stores/SKILL.md](skills/synthesize-stores/SKILL.md).
- **`synthesize-pois`** — competitors and home buildings → `competitor.tsv`,
  `home_building.tsv`. See
  [pipeline/skills/synthesize-pois/SKILL.md](skills/synthesize-pois/SKILL.md).
- **`synthesize-events`** — people-gathering events → `event.tsv`. See
  [pipeline/skills/synthesize-events/SKILL.md](skills/synthesize-events/SKILL.md).
- **`synthesize-sales`** — daily store sales (bowls), built from a
  weekday/weekend baseline plus home-building, competitor, event, temperature and
  rain influences → `sales.tsv`. See
  [pipeline/skills/synthesize-sales/SKILL.md](skills/synthesize-sales/SKILL.md).
