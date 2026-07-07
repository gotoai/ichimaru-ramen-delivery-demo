# Ichimaru agent-service — setup & web-API tasks.
# Run from the agent-service/ directory: `make` (or `make help`) to list targets.
# This service is SELF-CONTAINED with its own venv (agent-service/.venv) — see INSTALL.md.

# Prefer the service virtualenv (.venv) if present, else fall back to system python3.
PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# Dev-server bind address (used by `make dev` only; `make serve` reads API_HOST/API_PORT
# from .env). Override on the command line, e.g. `make dev PORT=9000`.
HOST ?= 127.0.0.1
PORT ?= 8000

.DEFAULT_GOAL := help
.PHONY: help install smoke test-serve test-serve-self-loaded serve dev

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install Python deps (NOTE: install torch/torchvision FIRST and separately — see INSTALL.md)
	$(PYTHON) -m pip install -r requirements.txt

smoke: ## Smoke test: load the model + one reply + peak VRAM (needs the GPU + downloaded model)
	$(PYTHON) tests/smoke_test.py

test-serve: ## Black-box test: HTTP client against a RUNNING server (start `make serve` first; override PORT=)
	$(PYTHON) -m pytest tests/test_api_client.py -q -s --port $(PORT)

test-serve-self-loaded: ## In-process test through the REAL model via TestClient (loads its own model; needs GPU)
	$(PYTHON) -m pytest tests/test_api_self_loaded.py -q -s

serve: ## Run the web API (single worker, FOREGROUND/blocking; host/port/key from .env). Ctrl-C to stop.
	# Single worker on purpose: the model is one per process (multiple workers = multiple
	# copies in VRAM). Blocks until Ctrl-C. To detach, run in tmux or under systemd/docker
	# rather than backgrounding make (a `make serve &` can orphan the GPU process on kill).
	$(PYTHON) -m agent.api

dev: ## Run the web API with code auto-reload for development (uvicorn --reload; override with HOST=/PORT=)
	# --reload re-loads the whole process (and the model) on every code change — handy but
	# not free (seconds + VRAM churn). Use `make serve` for a normal run.
	$(PYTHON) -m uvicorn agent.api:app --reload --host $(HOST) --port $(PORT)
