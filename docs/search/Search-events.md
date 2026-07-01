## Search Events

Web-search for near-future public events (祭り・花火大会・イベント・コンサート …)
around each Ichimaru location, via the Tavily search API, and write the results as a
structured TSV. This is a **live** step: the result depends on when it is run and is
not reproducible (like `search-weather-forecast`).

### Search service & secret

Use the Tavily web search API through its Python SDK. The API key is read from the
`.env` file at the repo root, key `TAVILY_API_KEY` (`.env` is git-ignored — never
commit or print the key).

  - Load the key from `.env` (parse it with the standard library, or use
    `python-dotenv`) into the environment and construct the client with the keyword
    argument: `TavilyClient(api_key=os.environ["TAVILY_API_KEY"])`.
  - If the key is missing/empty, fail with a clear message (do **not** log the key
    value).

### Dependencies

Add the SDK to `requirements.txt` (pinned, per repo convention) rather than an inline
`pip install`:

```
tavily-python==<pin to the installed version>   # run `pip install tavily-python`, then `pip show tavily-python`
```

Install with `pip install -r requirements.txt` inside the activated `.venv`.

### Locations to search

Extract the **distinct** locations from `DATA/s03_primary/store.tsv` and search once
per distinct location (currently ~44 locations across 80 stores — each is one
"advanced" Tavily call, so mind the API cost/quota).

**Location extraction (prefecture → 市区町村, dropping 政令市の行政区 but keeping
東京特別区).** For each store, strip the leading `prefecture` (it is a separate column
in `store.tsv`, so remove it as a prefix — do not re-detect it inside the name), then
from the remainder take:

  1. up to and **including the first `市`**; else
  2. if there is no `市`, up to and including the first `区` (Tokyo special ward);
     else
  3. up to and including the first `町` / `村`.

Prepend the prefecture. This drops designated-city administrative wards automatically
(they always follow the `市`) while preserving Tokyo's special wards (which have no
preceding `市`). Worked examples over the real data:

| store_name | extracted location |
| --- | --- |
| `千葉県千葉市稲毛区小仲台店` | `千葉県千葉市` (drop 稲毛区, follows 市) |
| `埼玉県さいたま市南区南浦和店` | `埼玉県さいたま市` (drop 南区) |
| `神奈川県横浜市港北区篠原北店` | `神奈川県横浜市` (drop 港北区) |
| `東京都世田谷区岡本店` | `東京都世田谷区` (**keep** 世田谷区 — special ward, no 市) |
| `東京都八王子市明神町店` | `東京都八王子市` (市, no ward) |
| `埼玉県伊奈町学園店` | `埼玉県伊奈町` (町) |

### Query design

Derive the target window from "today" (JST) so events line up with the
demand-forecast horizon (the coming week; keep a `--today` override, like the other
skills). Tavily filters by *publish* date, not by *event* date, so encode the date
window in the **query text** (the concrete month / date range) and let the downstream
agent parse actual event dates from the results.

Primary query template (per location `LOC`, for the coming week `START`–`END`):

```
{LOC}で{YYYY}年{M}月（{START}〜{END}）に開催されるイベント・祭り・花火大会・コンサート・マルシェ
```

Call with explicit parameters rather than relying on the query text alone:

```python
response = client.search(
    query=query,
    search_depth="advanced",
    topic="general",
    max_results=10,
    include_answer=False,
    country="japan",          # if supported by the installed SDK version
)
```

Alternative / supplementary queries if a single query gives thin recall (document
whichever set is actually used):

  - `{LOC} 今週末 イベント` — colloquial "this weekend" phrasing.
  - `{LOC} 花火大会 祭り {M}月` — category-focused for the seasonal drivers.

`response` is a Python **dict** (not a JSON string).

### Robustness

  - Wrap each location's search in `try/except`; retry transient errors a few times
    with backoff, and on final failure **skip that location and continue** — one bad
    location must not abort the batch.
  - Print a run summary to stdout (locations searched, results written, failures), and
    record which locations failed (a companion `searched_events_errors.tsv` or log
    lines) — but never include the API key.

### Output

A UTF-8, tab-separated file with a header row, **one row per search result**, at
`DATA/s08_search/searched_events.tsv` (create `DATA/s08_search/` if missing).
Columns:

  - `location` — the extracted 都県+市区町村 (the join key back to stores).
  - `query` — the exact query string sent.
  - `fetched_at` — ISO-8601 JST timestamp of the run.
  - `title` — result title.
  - `url` — result URL.
  - `content` — Tavily result snippet/content.
  - `score` — Tavily relevance score.
  - `published_date` — result published date if present (blank otherwise).

Because these are TSV values, **sanitise tabs/newlines** in `title`/`content`/`url`
(replace with spaces) so each result stays on one line. Keep the full nested response
out of the TSV; if full fidelity is wanted, additionally dump the raw dicts to a
companion `searched_events_raw.jsonl` (one JSON object per line).

### Downstream use (consumed by a separate agent)

This skill only **retrieves**; a separate agent program consumes the TSV. To make that
easy:

  - `location` is the join key — the consuming agent maps a location back to its
    stores by running the **same** extraction over `store.tsv`.
  - The agent reads `title`/`content`/`url` and extracts the structured event fields
    it needs — `event_name`, `event_date`(s), `venue`, and an expected-attendance /
    scale estimate — and, if it needs store-level proximity (the synthetic pipeline
    models events as geolocated points affecting stores within ~200 m), geocodes the
    venue to lat/long. Retrieval at 市区町村 granularity is the input; turning free
    text into a dated, geolocated, sized event is the consuming agent's job.
  - Keeping `published_date` and the full `url`/`content` now avoids a re-fetch later.
