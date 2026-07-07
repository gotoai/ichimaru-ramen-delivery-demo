"""Self-loaded integration test for agent.api — TestClient runs the app IN-PROCESS.

    make test-serve-self-loaded    # or: python -m pytest tests/test_api_self_loaded.py -q -s

TestClient calls the ASGI app object directly (no separate server, no port), but it loads
the model in THIS process. For the black-box variant — a pure HTTP client against a
separately running `make serve` — see tests/test_api_client.py (`make test-serve`).

agent-service is a GPU-mandate service, so this exercises the actual Gemma model: model
load + generation + JSON parsing, end to end, plus the HTTP contract (auth, validation,
response shape, error mapping).

Requires a CUDA GPU with enough free VRAM for the model (~5-6GB in 4-bit) and the model
in HF_HOME. NOTE: if a `python -m agent.api` server is already running it holds its own
copy in VRAM and this test's copy may be CPU-offloaded (slow) or OOM — stop it first.
Skips automatically when no CUDA device is present.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import `agent` without install

torch = pytest.importorskip("torch")
if not torch.cuda.is_available():  # no GPU -> skip the module rather than error
    pytest.skip("no CUDA device available", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from agent import api                       # noqa: E402
from agent.tasks import extract_events       # noqa: E402

AUTH_KEY = "agt_testkey_123"

# Two unambiguous Japanese events, in the shape search results arrive in. max_new_tokens is
# capped so generation stays quick — a couple of events need nowhere near the 2048 default.
REQUEST_BODY = {
    "location": "東京都世田谷区",
    "max_new_tokens": 512,
    "items": [
        {"title": "第46回 世田谷区たまがわ花火大会",
         "content": "2026年8月1日（土）、世田谷区の二子玉川緑地運動場で花火大会を開催。"
                    "約6000発の花火が打ち上げられます。",
         "url": "https://example.com/tamagawa-hanabi",
         "published_date": "2026-06-20"},
        {"title": "世田谷区民まつり 2026",
         "content": "2026年7月5日（日）〜6日（月）、馬事公苑にて世田谷区民まつりを開催します。",
         "url": "https://example.com/kumin-matsuri",
         "published_date": "2026-06-25"},
    ],
}


@pytest.fixture(scope="module")
def client():
    """Real model, loaded lazily on the first request. Bearer auth enabled with AUTH_KEY."""
    api.config.API_EAGER_LOAD = False
    api.config.GOTOAI_AGENT_API_KEY = AUTH_KEY
    with TestClient(api.app) as c:
        yield c


HEADERS = {"Authorization": f"Bearer {AUTH_KEY}"}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_extract_requires_auth(client):
    assert client.post("/v1/extract-events", json=REQUEST_BODY).status_code == 401


def test_extract_validation_error(client):
    r = client.post("/v1/extract-events", json={"items": []}, headers=HEADERS)
    assert r.status_code == 422  # pydantic min_length=1


def test_extract_events_real_model(client):
    resp = client.post("/v1/extract-events", json=REQUEST_BODY, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Metadata contract.
    assert data["item_count"] == 2
    assert data["location"] == "東京都世田谷区"
    assert data["prompt_version"] == extract_events.PROMPT_VERSION

    # The model should extract at least one of the two clearly-stated events.
    print("\nextracted events:", data["events"])   # visible with `pytest -s`
    assert data["event_count"] >= 1
    for ev in data["events"]:
        assert ev["event_name"]                       # non-empty
        assert ev["event_type"] in extract_events.EVENT_TYPES

    # After a generation the model is loaded, so readiness must now be green.
    assert client.get("/readyz").status_code == 200
