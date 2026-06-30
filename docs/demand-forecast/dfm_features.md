## Demand Forecast Model Features

Demand Forecast Model (DFM) features are the data set used for building the
machine-learning model for demand forecasting as well as for predicting future
sales.

### Time horizon definition

- **Reference date**
  The reference date is the date used as the anchor time point to organize the
  data as a time series in an unambiguous way. Generally, the reference date
  equals the date when data are processed, a.k.a. the data-processing date.

  In this system, a reference date is always defined as the **Thursday** of each
  week, which is the assumed date with a weekly cadence for data processing,
  feature engineering, model building, and prediction.

- **Time horizon of inference**
  Training and prediction data sets use the same time-horizon structure. The
  difference is that in training the errors between inferred values and ground
  truth are fed back to tune the model parameters, while in prediction only
  feed-forward processing is performed.

  There are 3 zones in the related time horizon:

  - **past zone (or left zone)**
    This zone is before the reference date on the time line. A past zone is
    associated with an end date equal to the **Sunday of the previous week**
    relative to the reference date.

  - **future zone (or right zone)**
    This zone is after the reference date on the time line and usually covers the
    period of interest for forecasting. A future zone is associated with a start
    date equal to the **Monday of the next week** relative to the reference date.

  - **middle zone**
    This zone contains the reference date and does not overlap the past or future
    zones. It is the week (Monday–Sunday) that contains the reference date, also
    called the **reference week**. The reference week is called **"week+0"** for
    convenience; the week immediately before it is **"week-1"**, the week before
    that **"week-2"**, …, and the week immediately after it is **"week+1"**, etc.

  Concretely, with the reference date `R` (a Thursday):
  - reference week (week+0): Monday `R-3` … Sunday `R+3`
  - week-k: Monday `R-3-7k` … Sunday `R+3-7k` (so week-1 = `R-10` … `R-4`,
    week-4 = `R-31` … `R-25`)
  - week+1: Monday `R+4` … Sunday `R+10`

### Conventions

- **ISO weekday number**: `1`=Monday, `2`=Tuesday, …, `6`=Saturday, `7`=Sunday.
  This numbering is used consistently across regression, weather, and calendar
  variables.
- **Weekday vs. weekend**: weekend = ISO weekday number ∈ {6, 7} (Saturday,
  Sunday); weekday = ISO weekday number ∈ {1, 2, 3, 4, 5}.
- **`[1-7]` expansion**: a `[1-7]` token in a variable name expands to 7 separate
  columns, one per ISO weekday number. For example,
  `week-1to4_weekday[1-7]_avg_sales` expands to `week-1to4_weekday1_avg_sales`,
  `week-1to4_weekday2_avg_sales`, …, `week-1to4_weekday7_avg_sales`.

### Key columns

These columns uniquely identify one data point (one row) in the feature data
set. The row granularity is **one row per (store, reference date, target
date)** — i.e. one row per single target day.

  - `store_name`
  The name of the store.
  - `reference_date`
  The reference date (a Thursday).
  - `target_date`
  The target date for which the forecast is made. The target date ranges over
  the 7 days of the immediate next week (week+1) after the reference date, i.e.
  `R+4` (Monday) … `R+10` (Sunday).

### Feature variables

Groups of feature variables:
  - Regression variables
  - Weather variables
  - Calendar variables

#### Regression variables

All regression variables take data only from the past zone, are constant across
the 7 target-date rows of the same `(store, reference_date)`, and are numeric.
Sales values come from `DATA/s03_primary/sales.tsv` for the matching
`store_name`. "weekday" / "weekend" follow the convention above.

Per-week level features (the week previous to the reference week):

  - `week-1_avg_sales` — mean of daily sales in week-1.
  - `week-1_median_sales` — median of daily sales in week-1.
  - `week-1_avg_weekday_sales` — mean of week-1 weekday (Mon–Fri) daily sales.
  - `week-1_median_weekday_sales` — median of week-1 weekday daily sales.
  - `week-1_avg_weekend_sales` — mean of week-1 weekend (Sat–Sun) daily sales.
  - `week-1_median_weekend_sales` — median of week-1 weekend daily sales.

Four-week level features (the 4 weeks previous to the reference week, week-1 …
week-4 pooled):

  - `week-1to4_avg_sales` — mean of all daily sales across weeks 1–4.
  - `week-1to4_median_sales` — median of all daily sales across weeks 1–4.
  - `week-1to4_avg_weekday_sales` — mean of weekday daily sales across weeks 1–4.
  - `week-1to4_median_weekday_sales` — median of weekday daily sales across weeks 1–4.
  - `week-1to4_avg_weekend_sales` — mean of weekend daily sales across weeks 1–4.
  - `week-1to4_median_weekend_sales` — median of weekend daily sales across weeks 1–4.

Delta features (trend between the nearest and the farthest of the four weeks,
computed as **`w-1_sub_w-4`** = week-1 value minus week-4 value, where each
week's value is the mean of its daily sales):

  - `delta-week-1to4_avg_sales` — `week-1_avg_sales` minus `week-4_avg_sales`.
  - `delta-week-1to4_weekday_avg_sales` — week-1 weekday mean minus week-4 weekday mean.
  - `delta-week-1to4_weekend_avg_sales` — week-1 weekend mean minus week-4 weekend mean.

Per-weekday level features:

  - `week-1to4_weekday[1-7]_avg_sales` — 7 columns; for each ISO weekday number,
    the mean of the (up to 4) daily sales that fall on that weekday within weeks
    1–4.

#### Weather variables

Weather variables sit in the future zone. They describe the weather on the
**`target_date`** (one value per row).

**Forecast-unavailable proxy.** Because a real weather forecast is treated as
unavailable at feature-processing time, the weather of `target_date` is
approximated from history. This proxy is applied **identically in training and
prediction** so that weather availability is the same at train time and serve
time — do **not** "fix" this by substituting the actual week+1 weather, which
would be label-adjacent leakage at serve time. The proxy logic:
  - Take the date in the **previous calendar year** with the same month and day
    as `target_date`. If that day does not exist in the previous year (e.g.
    `Feb 29` when the previous year is not a leap year), use the day before it
    (`Feb 28`).
  - If the value is missing in that previous-year record, fill from up to the
    **2 preceding days**. If still missing, leave the variable as a missing
    value.

**Station selection (store ↔ weather join).** Mirrors the `synthesize-sales`
skill: weather observations live in `DATA/s02_intermediate/weather_history_*.tsv`
(keyed by the Japanese station name `観測地点`), and station coordinates live in
`DATA/s02_intermediate/weather-station.tsv` (active stations only,
`End Date = 9999-99-99`), matched by station name (`Station Name (Kanji)`). For
each store, use the **nearest** station (by haversine distance, Earth radius
6,371,000 m) that has both coordinates and observations. The store's English
prefecture is not needed for the join — selection is by global nearest matched
station — but note the weather files use Japanese prefecture names.

All weather variables are numeric:

  - `week+1_high_temperature` — the `最高気温(℃)` column value for the proxy date.
  - `week+1_avg_temperature` — the `平均気温(℃)` column value for the proxy date.
  - `week+1_rainfall` — nominally the `降水量の合計(mm)` column, but **always set
    to 0** (the no-rain default). The one-year-lag proxy carries essentially no
    usable rainfall signal, so rainfall falls back to "no rain" rather than being
    proxied like the temperature columns.

#### Calendar variables

Calendar variables sit in the future zone and describe the `target_date`. They
derive purely from the calendar, so they have no missing values.

  - `month_number` — the month of the target date, 1–12 (January–December).
  - `is_weekend` — 1 if the target date is a weekend (ISO weekday number ∈ {6, 7}),
    else 0.
  - `weekday_number` — ISO weekday number of the target date, 1–7 (Monday–Sunday).
  - `target_offdays_cos` — `cos[2*PI*D/366]`, where `D` is the number of days from
    January 1st of the target date's year to the target date (`D = 0` on Jan 1st).
  - `target_offdays_sin` — `sin[2*PI*D/366]`, with `D` defined as above.

### Target variable

The target variable is the actual daily sales for the `(store_name, target_date)`
pair, named **`actual_sales`**. Its source is the `sales` column of
`DATA/s03_primary/sales.tsv` (renamed to `actual_sales` in the feature set). It
is a missing value in the prediction data set (see below).

### Data set time range

The data set time range is expressed as a range of reference dates:
  - **First valid reference date**: the first Thursday `R` such that the Monday of
    week-4 (`R-31`) is on or after the sales-data start date — so that every
    `week-1 … week-4` lookback is fully populated.
  - **Last valid reference date**: the last Thursday on or before the current
    system date in JST.

### Data set split

  - **Test data set**
    The last test reference date is the Thursday in the week **two weeks before**
    the current system date in JST. The first test reference date is the Thursday
    **7 weeks before** that last one (8 reference dates in total).

  - **Training data set**
    All reference dates from the first valid reference date up to the latest
    reference date **immediately before the first test reference date**. (This
    keeps training strictly earlier than the test window — no gap-week or
    post-test reference dates leak into training.)

  - **Prediction data set**
    Defined relative to the current system date in JST:
    - If the current date is **on or after Thursday** (Thursday–Sunday), this
      week's Thursday is already a valid reference date, so the data set contains
      the **last two valid reference dates**: this week's Thursday and the
      previous week's Thursday.
    - If the current date is **before Thursday** (Monday–Wednesday), this week's
      Thursday has not yet occurred, so the data set contains only the **single
      last valid reference date**: the previous week's Thursday.

    The target variable (`actual_sales`) is unavailable for week+1 and is
    therefore left as a missing value.

### Output

UTF-8, tab-separated files (key columns + feature variables + target):

  - **Training data set** — `DATA/s04_feature/training_dataset.tsv`
  - **Test data set** — `DATA/s04_feature/test_dataset.tsv`
  - **Prediction data set** — `DATA/s04_feature/predict_dataset.tsv`

The `DATA/s04_feature/` directory is created if it does not exist.
