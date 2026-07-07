# web-app — area-manager dashboard. CPU-only; no model (chat is proxied to agent-service).
# Create the venv first:  python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help venv install geojson serve dev test clean

help:
	@echo "make install  - create venv and install requirements"
	@echo "make geojson  - (re)build the prefecture outline GeoJSON cache"
	@echo "make serve    - run the dashboard (host/port from .env)"
	@echo "make dev      - run with autoreload"
	@echo "make test     - data-layer + endpoint smoke tests (no model needed)"

$(VENV):
	python3.12 -m venv $(VENV)

install: $(VENV)
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt

geojson:
	$(PY) -m app.geo

serve:
	$(PY) -m app.main

dev:
	$(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

test:
	$(PY) -m pytest -q

clean:
	rm -rf app/__pycache__ app/**/__pycache__ .pytest_cache
