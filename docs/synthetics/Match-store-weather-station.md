## Match Store to Weather Station

Match each Ichimaru store to the single weather station whose observations
represent that store's weather, and persist the mapping. This is the **same**
store→station assignment the Demand Forecast Model already uses for its weather
features (computed on the fly, but not persisted, inside
`dfm-create-features` / `create_features.py`). Persisting it here lets downstream
consumers — notably the residuals / diagnosis analysis
(`docs/diagnosis/Residuals.md`) — join a store's *actual* weather using exactly
the station the model's proxy was drawn from.

### Assignment rule

For each store, pick the **nearest usable weather station** by great-circle
(haversine) distance, where a *usable* station is one that is:

  - **active** — `End Date` = `9999-99-99` in
    `DATA/s02_intermediate/weather-station.tsv`;
  - **temperature-capable** — has a temperature sensor, i.e. the `Temperature`
    flag in that same file equals `1`. **Stations without a temperature sensor
    (`Temperature` != `1`, e.g. precipitation-only AMeDAS rain gauges) are removed
    before matching.** This is an explicit processing rule: a store must never be
    matched to a station that cannot report temperature, otherwise its actual
    temperature would be missing and no temperature comparison / diagnosis is
    possible;
  - **georeferenced** — has `Latitude_Precipitation` / `Longitude_Precipitation`
    in that same file;
  - **observed** — actually appears (column `観測地点`) in the weather-history
    files `DATA/s02_intermediate/weather_history_*.tsv`, so a store is never
    matched to a station that has no observations.

Store coordinates come from `latitude` / `longitude` in
`DATA/s03_primary/store.tsv`. These criteria (active + temperature-capable +
georeferenced + observed, nearest by haversine) are **identical** to those used by
[synthesize-sales](../../ai/skills/synthesize-sales/scripts/synthesize_sales.py) and
[create_features.py](../../ai/skills/dfm-create-features/scripts/create_features.py),
so each store's synthetic-sales weather, model feature weather, and residual actual
weather all resolve to the same station.

### Inputs

  - `DATA/s03_primary/store.tsv` — stores with coordinates (from `synthesize-stores`).
  - `DATA/s02_intermediate/weather-station.tsv` — JMA station master (from
    `retrieve-weather-station`).
  - `DATA/s02_intermediate/weather_history_*.tsv` — to restrict to observed
    stations (from `retrieve-weather-history`).

### Output

A UTF-8, tab-separated file with a header row, one row per store (ordered by
`store_name`), at
`DATA/s03_primary/matched_store_weather_station.tsv`:

  - `prefecture`
  - `store_name`
  - `store_latitude`
  - `store_longitude`
  - `station_number` — JMA station number of the matched station.
  - `station_name` — matched station name; equals the `観測地点` value in the
    weather-history files (the join key for actual weather).
  - `station_latitude`
  - `station_longitude`
  - `distance_m` — store-to-station great-circle distance in metres.

### Notes

  - Standard library only; deterministic for fixed inputs.
  - Depends on the `synthesize-stores`, `retrieve-weather-station`, and
    `retrieve-weather-history` outputs.
