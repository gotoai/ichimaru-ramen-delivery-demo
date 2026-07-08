# Third-Party Notices

This repository's own source code is released under the [MIT License](LICENSE). It also
**bundles**, **downloads**, or **consumes** third-party software and data that remain under
their own licenses and terms of use. Those terms — not this repository's MIT license — govern
each item below. Review them before redistributing any bundled or downloaded artifacts.

## Bundled JavaScript / CSS (`web-app/app/static/vendor/`)

These libraries are vendored (checked in) to keep the demo self-contained and CDN-free.
Their original license headers are preserved in the minified files.

| Library | Version | License | Project |
|---|---|---|---|
| Apache ECharts | 5.6.0 | Apache License 2.0 | https://echarts.apache.org/ |
| Leaflet | 1.9.4 | BSD 2-Clause | https://leafletjs.com/ |
| htmx | 2.0.4 | BSD 2-Clause (Zero-Clause) | https://htmx.org/ |

- Apache ECharts — Copyright © The Apache Software Foundation. Licensed under the Apache
  License, Version 2.0: <https://www.apache.org/licenses/LICENSE-2.0>. See the license
  header retained in `web-app/app/static/vendor/echarts.min.js`.
- Leaflet — Copyright © 2010–2024 Volodymyr Agafonkin; © 2010–2011 CloudMade. Licensed under
  the BSD 2-Clause License: <https://github.com/Leaflet/Leaflet/blob/main/LICENSE>.
- htmx — Copyright © Big Sky Software. Licensed under the BSD 2-Clause / Zero-Clause BSD
  license: <https://github.com/bigskysoftware/htmx/blob/master/LICENSE>.

## Model (downloaded at run time, not bundled)

| Model | License / Terms | Source |
|---|---|---|
| Gemma 4 12B (`google/gemma-4-12B-it`) | Gemma Terms of Use | https://ai.google.dev/gemma/terms |

The model weights are **not** included in this repository; `agent-service` downloads them
from Hugging Face into a local cache. Use of Gemma is subject to Google's Gemma Terms of Use
and Prohibited Use Policy.

## Data sources (downloaded at run time, not bundled)

The pipeline downloads open Japanese government data and calls third-party APIs. None of the
downloaded data is committed to this repository (`DATA/` is git-ignored). Each source is used
under its own terms of use:

| Source | Terms |
|---|---|
| 政府統計の総合窓口 (e-Stat) — 2020 Census population & boundary data | e-Stat / 政府統計 terms of use |
| 国土数値情報 (MLIT National Land Numerical Information) | MLIT terms of use |
| 気象庁 (JMA) — weather history & forecast | JMA terms of use |
| Tavily Search API | Tavily API terms of service |
| Google Geocoding API | Google Maps Platform terms of service |

Downloaded government data may carry attribution or redistribution requirements; consult each
provider's terms before redistributing any retrieved data. This project retrieves such data at
run time and does not redistribute it.
