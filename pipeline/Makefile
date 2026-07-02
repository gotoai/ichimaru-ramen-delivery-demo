# Ichimaru ramen delivery demo — setup & data tasks.
# Run from the pipeline/ directory: `cd pipeline && make` (or `make -C pipeline`).
# Run `make` (or `make help`) to list available targets.

# Prefer the project virtualenv (pipeline/.venv) if present, else fall back to system python3.
PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.DEFAULT_GOAL := help
.PHONY: help base-data synthetics features modeling prediction diagnosis search calibration

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

calibration: ## Calibrate predicted sales for the weather forecast (bias + net weather slopes) -> DATA/s09_calibration/
	# Requires the `prediction` and `diagnosis` outputs plus the weather forecast from `search`.
	$(PYTHON) skills/calibrate-for-weather/scripts/calibrate_for_weather.py
