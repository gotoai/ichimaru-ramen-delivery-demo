"""Runtime configuration for agent-service.

IMPORTANT: importing this module loads agent-service/.env into the environment. It
must be imported BEFORE torch/transformers, because huggingface_hub reads HF_HOME
once at import time — otherwise the model would re-download to the default cache.
`agent.llm` imports this module before importing torch, so that ordering holds.
"""
from __future__ import annotations

import os
from pathlib import Path

# Load agent-service/.env (this file is agent-service/agent/config.py -> parents[1]).
try:
    from dotenv import load_dotenv

    _env = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(_env if _env.exists() else None)
except ImportError:  # python-dotenv not installed yet (e.g. syntax-only checks)
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Reduce CUDA fragmentation OOMs (must be set before torch initializes CUDA; this module
# is imported before torch in agent.llm). Harmless if already set by the user.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

MODEL_ID = os.environ.get("MODEL_ID", "google/gemma-4-E4B-it")
HF_HOME = os.environ.get("HF_HOME", "")          # informational; already applied via .env
MAX_NEW_TOKENS = _int("MAX_NEW_TOKENS", 2048)
GEN_TEMPERATURE = _float("GEN_TEMPERATURE", 0.7)
GEN_TOP_P = _float("GEN_TOP_P", 0.95)

# Web-API bearer key (used later, not by the CLI/spike).
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")

# Repo root (agent-service/agent/config.py -> parents[2]), for reading DATA/*.
REPO_ROOT = Path(__file__).resolve().parents[2]
