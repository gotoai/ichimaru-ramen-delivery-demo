"""Black-box test for agent.api — a pure HTTP client against a RUNNING server.

    make serve                       # terminal 1: start the server (loads the model)
    make test-serve                  # terminal 2: hit it over real HTTP on port 8000
    make test-serve PORT=9000        # ...or a different port

Unlike tests/test_api_self_loaded.py (TestClient, in-process), this starts NO server and
loads NO model — it exercises the real deployed server end to end: uvicorn, sockets, HTTP,
bearer auth, and the /v1 interface. The server's own model copy serves the request, so
there is no second copy in VRAM.

It SKIPS cleanly if no server is listening on the target port, and reads GOTOAI_AGENT_API_KEY
from the same config the server uses, so the bearer token stays in sync.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import `agent` without install

from agent import config                 # noqa: E402  GOTOAI_AGENT_API_KEY + default port (server's source)
from agent.tasks import estimate_attendance, extract_events  # noqa: E402

# Two unambiguous Japanese events; capped tokens so generation stays quick.
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
def base_url(server_port) -> str:
    """Base URL of the running server; skip the module if nothing is listening there."""
    url = f"http://127.0.0.1:{server_port}"
    try:
        httpx.get(f"{url}/healthz", timeout=2.0)
    except httpx.TransportError:
        pytest.skip(f"no agent.api server reachable at {url} — start one with "
                    f"`make serve` (PORT={server_port}).")
    return url


@pytest.fixture(scope="module")
def headers() -> dict:
    """Bearer header when the server has a key set; empty when auth is disabled."""
    return {"Authorization": f"Bearer {config.GOTOAI_AGENT_API_KEY}"} if config.GOTOAI_AGENT_API_KEY else {}


def test_healthz(base_url):
    r = httpx.get(f"{base_url}/healthz", timeout=5)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_extract_requires_auth(base_url):
    if not config.GOTOAI_AGENT_API_KEY:
        pytest.skip("server started without GOTOAI_AGENT_API_KEY — auth disabled, nothing to reject")
    r = httpx.post(f"{base_url}/v1/extract-events", json=REQUEST_BODY, timeout=30)  # no header
    assert r.status_code == 401


def test_extract_validation_error(base_url, headers):
    r = httpx.post(f"{base_url}/v1/extract-events", json={"items": []}, headers=headers, timeout=30)
    assert r.status_code == 422  # pydantic min_length=1


def test_extract_events_real_model(base_url, headers):
    # Generous timeout: the first request may trigger a lazy model load on the server.
    r = httpx.post(f"{base_url}/v1/extract-events", json=REQUEST_BODY, headers=headers, timeout=300)
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["item_count"] == 2
    assert data["location"] == "東京都世田谷区"
    assert data["prompt_version"] == extract_events.PROMPT_VERSION

    print("\nextracted events:", data["events"])   # visible with `pytest -s`
    assert data["event_count"] >= 1
    for ev in data["events"]:
        assert ev["event_name"]                       # non-empty
        assert ev["event_type"] in extract_events.EVENT_TYPES


ESTIMATE_BODY = {
    "context": {"地域": "東京都世田谷区"},
    "max_new_tokens": 512,
    "events": [
        {"event_name": "第46回 世田谷区たまがわ花火大会", "event_type": "fireworks",
         "start_date": "2026-08-01", "venue": "二子玉川緑地運動場", "location": "世田谷区"},
        {"event_name": "世田谷区民まつり 2026", "event_type": "festival",
         "start_date": "2026-07-05", "venue": "馬事公苑", "location": "世田谷区"},
    ],
}


def test_estimate_requires_auth(base_url):
    if not config.GOTOAI_AGENT_API_KEY:
        pytest.skip("server started without GOTOAI_AGENT_API_KEY — auth disabled, nothing to reject")
    r = httpx.post(f"{base_url}/v1/estimate-attendance", json=ESTIMATE_BODY, timeout=30)  # no header
    assert r.status_code == 401


def test_estimate_validation_error(base_url, headers):
    r = httpx.post(f"{base_url}/v1/estimate-attendance", json={"events": []},
                   headers=headers, timeout=30)
    assert r.status_code == 422  # pydantic min_length=1


def test_estimate_attendance_real_model(base_url, headers):
    r = httpx.post(f"{base_url}/v1/estimate-attendance", json=ESTIMATE_BODY,
                   headers=headers, timeout=300)
    assert r.status_code == 200, r.text
    data = r.json()

    # Metadata contract.
    assert data["event_count"] == 2
    assert data["prompt_version"] == estimate_attendance.PROMPT_VERSION

    # The model should estimate at least one of the two well-known event types.
    print("\nattendance estimates:", data["estimates"])   # visible with `pytest -s`
    assert data["estimate_count"] >= 1
    for est in data["estimates"]:
        assert est["event_name"]                          # non-empty
        att = est["expected_attendance"]
        assert set(att) == {"point", "low", "high"}       # range shape always present
        # Values are ints when the model produced them ("" when absent) — and a
        # fireworks/festival estimate should plausibly be a positive number.
        if isinstance(att["point"], int):
            assert att["point"] > 0


def test_readyz(base_url):
    # After a generation the server's model is loaded, so readiness is green.
    r = httpx.get(f"{base_url}/readyz", timeout=10)
    assert r.status_code == 200
