# Ichimaru ramen delivery demo — setup & data tasks.
# Run `make` (or `make help`) to list available targets.

# Prefer the project virtualenv if present, else fall back to system python3.
PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.DEFAULT_GOAL := help
.PHONY: help base-data synthetics

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

base-data: ## Download population, boundary shapefiles, weather stations, and weather history (setup step 1)
	$(PYTHON) ai/skills/retrieve-regional-population/scripts/retrieve_population.py
	$(PYTHON) ai/skills/retrieve-regional-geoshapes/scripts/retrieve_geoshapes.py
	$(PYTHON) ai/skills/retrieve-weather-station/scripts/retrieve_weather_station.py
	# JMA daily weather history, last 3 full calendar years — long step, ~4-6 min
	$(PYTHON) ai/skills/retrieve-weather-history/scripts/retrieve_weather.py

synthetics: ## Synthesize primary data (stores, competitors, home buildings, events) from base data (setup step 2)
	$(PYTHON) -m pip install -q -r requirements.txt
	$(PYTHON) ai/skills/synthesize-stores/scripts/synthesize_stores.py
	$(PYTHON) ai/skills/synthesize-pois/scripts/synthesize_pois.py
	$(PYTHON) ai/skills/synthesize-events/scripts/synthesize_events.py
