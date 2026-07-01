## Sales

Actual salses of each store, in units of bowls of ramen.

### Map distance algorithm
- Inputs: two pin points, each given as a latitude and longitude in degrees, and the Earth's radius in meters (commonly the mean radius, 6,371,000 m).

- Processes:
  - Step 1 — Convert to radians. Trigonometric functions operate on radians, so convert all four coordinates (both latitudes and both longitudes) from degrees to radians by multiplying each by π / 180.

  - Step 2 — Take the differences. Compute the difference in latitude (second latitude minus the first) and the difference in longitude (second longitude minus the first). These are the angular gaps between the two points along the north–south and east–west directions.

  - Step 3 — Apply the haversine of each difference. The "haversine" of an angle is sin²(angle / 2) — i.e., halve the angle, take its sine, and square it. Compute this for the latitude difference and for the longitude difference.

  - Step 4 — Combine into a single value (call it a). Add together:
  
    - the haversine of the latitude difference, and
    - the haversine of the longitude difference, scaled by the cosine of the first latitude times the cosine of the second latitude.
  
  That cosine scaling accounts for the fact that lines of longitude crowd together as you move away from the equator toward the poles, so a degree of longitude spans less ground at higher latitudes. The result a is the square of the half-chord between the two points.

  - Step 5 — Convert to the central angle. Take the arctangent-based inverse (specifically 2 × atan2(√a, √(1 − a)), which is a numerically stable way to get 2 × arcsin(√a)). This yields the central angle c — the angle, in radians, subtended at the Earth's center by the arc connecting the two points.

  - Step 6 — Scale by the radius. Multiply the central angle c by the Earth's radius in meters. Because arc length equals radius times central angle, this gives the surface distance between the two pins in meters.

### Sales sampling algorithm

The algorithm below computes the sales for a single (store, date). The skill applies it for **every store** in `DATA/s03_primary/store.tsv` over the date range `time_horizon/sales_history` in `config/config.yaml` (start = January 1st of the year two years before the end year; end = the day 2 days before the current system date in JST), producing one output row per (store, date).

#### Sampling and reproducibility
- All randomness draws from a single RNG seeded once with `synthetics/random_seed` (629) in `config/config.yaml`, consumed in a fixed order: stores in ascending `store_name`, dates ascending, and within a (store, date) the steps in order — baseline, then each home building, competitor and event (in their `*.tsv` file order), then temperature, then rain.
- `scale_sampling` (kernel `beta`, `normalize: yes`): take a Beta(alpha, beta) draw and divide it by its mean alpha/(alpha+beta) so the scale has expectation 1.0.
- `noise_sampling` (kernel `gaussian`): draw N(mu, sigma) and clip it to `bounds` [lo, hi].
- Sampling granularity: baseline scale/noise once per (store, date); home/competitor/event scale/noise once per (store, date, entity); temperature and rain scale/noise once per (store, date).
- Weekday vs weekend is determined by the date's day of week only — Saturday and Sunday are weekend; Japanese public holidays are ignored.

- Inputs:
  - Store attributes, fetched from the `DATA/s03_primary/store.tsv`
  - Date, in 'YYYY-MM-DD' format

- Other referenced informat:
  - Home building data
  - Comeptitor data
  - Event data
  - Weater data

- Processes
  1. Baseline
    - Select the initinal baseline value B according to the weekday/weekend of the target date.
    - Sample the sacle Sb, indicated by file `config/config.yaml`, `synthetics/sales/baseline/scale_sampling`
    - Sample the noise Nb, indicated by file `config/config.yaml`, `synthetics/sales/baseline/noise_sampling`
    - At then end of this process, the sales value Y is: y1 = B * Sb * Nb

  2. Home building influences
    - Select all home buildings, from `DATA/s03_primary/home_building.tsv`, and keep those that are **active on the target date** (open_date <= date <= end_date; end_date '9999-99-99' means open-ended) and whose map distance to the store is no greater than 500 meters

    - For each home building having number of units Uh, with map distance to the store Dh, calculate the additional demand factor by:
      Yh = Uh / 10 * Sh * (100 / (100 + Dh)) * Nh

    Where Sh and Nh is the sampled scale and noise indicated by file `config/config.yaml`, `synthetics/sales/home_building/[scale|noise]_sampling`

    - Update y1 to y2 by adding the additional demand factors to the sales:
      y2 = y1 + (Yh1 + Yh2 + ...)


  3. Competitor influences
    - Select all competitors, from `DATA/s03_primary/competitor.tsv`, and keep those that are **active on the target date** (open_date <= date <= end_date; end_date '9999-99-99' means open-ended) and whose map distance to the store is no greater than 50 meters

    - For each select the baseline sales Bc according to the weekday/weekend, with map distance to the store Dc, calculate the additional negative demand factor by:
      Yc = - Bc / 4 * Sc * (10 / (10 + Dc)) * Nc

    Where Sc and Nc is the sampled scale and noise indicated by file `config/config.yaml`, `synthetics/sales/competitor/[scale|noise]_sampling`

    - Update y2 to y3 by adding the additional demand factors to the sales:
      y3 = y2 + (Yc1 + Yc2 + ...)

  4. Event influences
    - Select all events, from `DATA/s03_primary/event.tsv`, and keep those whose **event_date equals the target date** and whose map distance to the store is no greater than 200 meters

    - For each select the number of people Pe from `event.tsv`, with map distance to the store De, calculate the additional demand factor by:
      Ye = Pe / 20 * Se * (50 / (50 + De)) * Ne

    Where Se and Ne is the sampled scale and noise indicated by file `config/config.yaml`, `synthetics/sales/event/[scale|noise]_sampling`

    - Update y3 to y4 by adding the additional demand factors to the sales:
      y4 = y3 + (Ye1 + Ye2 + ...)

  5. Temperature influences
    - Usable weather stations are those that are **active, temperature-capable, located, and observed**: from the station master `DATA/s02_intermediate/weather-station.tsv` keep only active stations (`End Date = 9999-99-99`) that have a temperature sensor (`Temperature` flag = `1`) and coordinates, then match them to the observation files `DATA/s02_intermediate/weather_history_all_*.tsv` (`観測地点`) by station name on a best-effort basis. **Stations without a temperature sensor (precipitation-only rain gauges) are removed before matching**, so every store is associated with a station that can report temperature.
    - Find the nearest such weather station W to the store (by map distance), and fetch the high temperature Th = `最高気温(℃)` of W on the target date. If the value is missing, fill it forward from the most recent value within the previous 3 days; if still missing, skip this processing (leave the sales unchanged for this factor).

    - Update y4 to y5 by applying the adjustment
      y5 = y4 * (1 + (20 - Th) * 0.02 * St) * Nt

    Where St and Nt is the sampled scale and noise indicated by file `config/config.yaml`, `synthetics/sales/weather_temperature/[scale|noise]_sampling`


  6. Rain influences
    - Using the same nearest usable weather station W as in step 5, fetch the rain volume Vr = `降水量の合計(mm)` of W on the target date. If the value is missing, fill it forward from the most recent value within the previous 3 days; if still missing, skip this processing (leave the sales unchanged for this factor).

    - Update y5 to y6 by applying the adjustment
      y6 = y5 * (30 / (30 + Vr) * Sr) * Nr

    Where Sr and Nr are the sampled scale and noise indicated by file `config/config.yaml`, `synthetics/sales/weather_rain/[scale|noise]_sampling`


#### Output
The final sales value y6 is rounded to the nearest integer (bowls are whole) and clamped to a minimum of 10 (never below 10).

Save the sales to `DATA/s03_primary/sales.tsv`
  - Columns layout:
    - prefecture
    - store_name
    - date
    - sales