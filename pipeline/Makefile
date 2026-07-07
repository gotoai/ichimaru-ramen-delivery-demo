# Ichimaru ramen delivery demo — setup & data tasks.
# Run from the pipeline/ directory: `cd pipeline && make` (or `make -C pipeline`).
# Run `make` (or `make help`) to list available targets.

# Prefer the project virtualenv (pipeline/.venv) if present, else fall back to system python3.
PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.DEFAULT_GOAL := help
.PHONY: help base-data synthetics features modeling prediction diagnosis search calibration analysis

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

base-data: ## Download population, boundary shapefiles, weather stations, and weather history (setup step 1)
	$(PYTHON) skills/retrieve-regional-population/scripts/retrieve_population.py
	$(PYTHON) skills/retrieve-regional-geoshapes/scripts/retrieve_geoshapes.py
	$(PYTHON) skills/retrieve-weather-station/scripts/retrieve_weather_station.py
	# JMA daily weather history, last 3 full calendar years — long step, ~4-6 min
	$(PYTHON) skills/retrieve-weather-history/scripts/retrieve_weather.py

synthetics: ## Synthesize primary data (stores, competitors, home buildings, events, sales; match weather stations) from base data (setup step 2)
	# Requires the `base-data` outputs in DATA/s02_intermediate/ — run `make base-data` first.
	$(PYTHON) -m pip install -q -r requirements.txt
	$(PYTHON) skills/synthesize-stores/scripts/synthesize_stores.py
	$(PYTHON) skills/synthesize-pois/scripts/synthesize_pois.py
	$(PYTHON) skills/synthesize-events/scripts/synthesize_events.py
	$(PYTHON) skills/synthesize-sales/scripts/synthesize_sales.py
	$(PYTHON) skills/match-store-weather-station/scripts/match_store_weather_station.py

modeling: ## Build DFM features then train/tune/evaluate the demand-forecast model (setup step 3)
	# Requires the `synthetics` outputs in DATA/s03_primary/ — run `make synthetics` first.
	$(PYTHON) -m pip install -q -r requirements.txt
	$(PYTHON) skills/dfm-create-features/scripts/create_features.py
	$(PYTHON) skills/dfm-build-model/scripts/build_model.py

prediction: ## Score the trained model on the prediction set, then SHAP-explain it -> DATA/s06_prediction/ (setup step 4)
	# Requires the `modeling` outputs in DATA/s05_model/ — run `make modeling` first.
	$(PYTHON) skills/dfm-predict-sales/scripts/predict_sales.py
	$(PYTHON) skills/dfm-explain-predictions/scripts/explain_predictions.py

diagnosis: ## Back-test, compute residuals (feature vs actual weather), then diagnose error slopes -> DATA/s07_diagnosis/
	# Requires the `modeling` outputs, plus the matched-weather file from `synthetics` — run `make synthetics modeling` first.
	$(PYTHON) skills/diagnosis-backtest/scripts/backtest_sales.py
	$(PYTHON) skills/diagnosis-calculate-residuals/scripts/calculate_residuals.py
	$(PYTHON) skills/diagnosis-calculate-slopes/scripts/calculate_slopes.py

search: ## Fetch live JMA weather forecast + web-search local events -> DATA/s08_search/
	# Live data. Weather: JMA forecast API (free; reissued ~05/11/17 JST).
	$(PYTHON) skills/search-weather-forecast/scripts/search_weather_forecast.py
	# Events: Tavily web search — needs TAVILY_API_KEY in pipeline/.env and spends API credits
	# (~one 'advanced' search per distinct location). Preview with --dry-run first.
	$(PYTHON) skills/search-events/scripts/search_events.py

calibration: ## Calibrate predicted sales for weather + nearby events, then merge -> DATA/s09_calibration/
	# Requires the `prediction` and `diagnosis` outputs plus the weather forecast and
	# searched events from `search`.
	# 1) Weather: divide out the diagnosed bias + temperature/rainfall slopes.
	$(PYTHON) skills/calibrate-for-weather/scripts/calibrate_for_weather.py
	# 2) Events: four independent skills — extract -> geo-code -> map-match -> estimate.
	#    LIVE / GPU: the agent-service web API must be running for extract + estimate
	#    (cd ../agent-service && make serve, wait for /readyz); geo-code spends Google Geocoding
	#    credits. Reads GOTOAI_AGENT_API_KEY / GOOGLE_GEOCODING_API_KEY from agent-service/.env.
	#    Preview extract with --limit-locations. Each step reads the previous step's JSON.
	$(PYTHON) skills/calibrate-for-events-extract/scripts/extract_events.py
	$(PYTHON) skills/calibrate-for-events-geo-code/scripts/geocode_events.py
	$(PYTHON) skills/calibrate-for-events-map-match/scripts/map_match_events.py
	$(PYTHON) skills/calibrate-for-events-estimate-attendance/scripts/estimate_attendance.py
	# 3) Merge: calibrated_sales = weather_calibrated_sales + event_added_demand (deterministic).
	$(PYTHON) skills/calibrate-merge/scripts/calibrate_merge.py

analysis: ## Build the clean, DuckDB-ready analytics layer -> DATA/s10_analysis/ (needs prediction + diagnosis + calibration)
	$(PYTHON) -m pip install -q -r requirements.txt
	# Core: deterministic reshapes of the upstream outputs into dims + facts.
	$(PYTHON) skills/build-analysis-layer/scripts/build_core.py
	# Marts: scorecard / interval / anomaly.
	$(PYTHON) skills/build-analysis-layer/scripts/build_marts.py
	# Typed DuckDB views + data dictionary generated from the same spec.
	$(PYTHON) skills/build-analysis-layer/scripts/build_schema.py
