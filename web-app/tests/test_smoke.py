"""Smoke tests: data layer + HTTP routes, no model required.

The chat route is exercised with agent_client.stream_chat monkeypatched, so these run
offline (no agent-service, no GPU).
"""
from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from app import agent_client, data, main

NAME = "埼玉県川口市川口店"
EVENT_DAY = "2026-06-29"  # KAWAGUCHI IDOL FESTIVAL day — has events + SHAP for the breakdown


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(main.app)


# ----------------------------------------------------------------- data layer
def test_data_loads():
    d = data.get_data()
    assert len(d.stores()) == 80
    assert data.tomorrow_jst().count("-") == 2  # ISO date


def test_forecast_is_rolling_from_tomorrow_and_integer():
    d = data.get_data()
    rows = d.forecast(NAME)
    assert len(rows) == 7
    # Always starts at/after tomorrow (JST) and is sorted ascending.
    assert rows[0]["target_date"] >= data.tomorrow_jst()
    assert [r["target_date"] for r in rows] == sorted(r["target_date"] for r in rows)
    # Each row carries the reference date it came from (window can span reference dates).
    assert all("reference_date" in r for r in rows)
    # All displayed estimates are whole numbers.
    for r in rows:
        for k in ("predicted_sales", "weather_calibrated_sales", "event_added_demand",
                  "calibrated_sales", "suggested_order"):
            assert isinstance(r[k], int), (k, r[k])


def test_extract_municipality():
    # The 市区町村 reduction is the join key between stores and the weather forecast
    # (fact_weather_forecast in the analytics layer).
    assert data.extract_municipality("埼玉県", "埼玉県川口市川口店") == "川口市"
    assert data.extract_municipality("千葉県", "千葉県市原市五井店") == "市原市"   # name-initial 市
    assert data.extract_municipality("東京都", "東京都新宿区西新宿店") == "新宿区"


def test_breakdown_resolves_reference_internally():
    d = data.get_data()
    bd = d.breakdown(NAME, EVENT_DAY)  # no ref argument — resolved from (store, target)
    assert bd is not None
    assert bd["self_check_ok"] is True
    assert bd["events"] and bd["top_shap"]
    assert isinstance(bd["calibrated_sales"], int)
    assert all(isinstance(s["shap_value"], int) for s in bd["top_shap"])


# ----------------------------------------------------------------- routes
def test_shap_explanation_payload():
    d = data.get_data()
    p = d.shap_explanation(NAME)
    assert p and len(p["上位要因"]) == 6
    f0 = p["上位要因"][0]
    assert {"要因", "特徴量の値", "予測への寄与_杯"} <= set(f0)
    assert f0["要因"] in data.FEATURE_LABELS.values()   # friendly label, not raw column


def test_shap_explain_stream(client: TestClient, monkeypatch):
    async def fake_stream(message, *, context=None, history=None, max_new_tokens=1024):
        assert context is not None and "上位要因" in context   # SHAP payload injected
        yield "週末"
        yield "の影響が大きいです。"

    monkeypatch.setattr(agent_client, "stream_chat", fake_stream)
    with client.stream("GET", f"/ui/store/{NAME}/shap/explain") as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert "週末" in body and "event: done" in body


def test_index_and_json(client: TestClient):
    assert client.get("/healthz").json()["status"] == "ok"
    page = client.get("/").text
    assert "ダッシュボード" in page and "予測期間" in page
    stores = client.get("/api/stores").json()
    assert len(stores) == 80 and isinstance(stores[0]["mean_calibrated"], int)
    gj = client.get("/api/geojson/prefectures").json()
    assert len(gj["features"]) == 4
    events = client.get("/api/events").json()
    assert len(events) >= 1 and "latitude" in events[0]


def test_fragments(client: TestClient):
    fc = client.get(f"/ui/store/{NAME}/forecast")
    assert fc.status_code == 200 and "fc-row" in fc.text
    bd = client.get(f"/ui/store/{NAME}/breakdown", params={"target": EVENT_DAY})
    assert bd.status_code == 200 and "wf-row" in bd.text and "SHAP" in bd.text


def test_series_404_for_unknown_store(client: TestClient):
    assert client.get("/api/store/存在しない店/series").status_code == 404


def test_shap_waterfall_is_additive(client: TestClient):
    wf = client.get(f"/api/store/{NAME}/shap").json()
    assert wf["target_date"] >= data.tomorrow_jst()          # defaults to tomorrow
    assert len(wf["items"]) == 8
    total = wf["base_value"] + sum(i["shap"] for i in wf["items"]) + wf["other"]
    assert abs(total - wf["predicted"]) < 1e-6               # base + Σcontrib == prediction
    assert client.get("/api/store/存在しない店/shap").status_code == 404


def test_precheck_rejects_meaningless():
    bad = ["", "  ", "A", "AAAAAAAAAAA", "abababab", "。。。。", "!!!!", "9999"]
    for m in bad:
        assert main._precheck_reply(m) is not None, m
    good = ["なぜ高い？", "why is friday high", "傾向は?", "天気"]
    for m in good:
        assert main._precheck_reply(m) is None, m


def test_chat_meaningless_skips_agent(client: TestClient, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("agent must not be called for meaningless input")

    monkeypatch.setattr(agent_client, "chat", boom)
    r = client.post("/ui/chat", data={"message": "AAAAAAAAAAA"})
    assert r.status_code == 200
    j = r.json()
    assert "質問の意味はわかりません" in j["message"] and j["tool_calls"] == 0


def test_chat_calls_agent_with_steps(client: TestClient, monkeypatch):
    async def fake_chat(message, *, context=None, history=None, include_steps=True, max_new_tokens=1024):
        assert context is not None and NAME in context  # selected store passed as reference
        return {"message": "回答テスト", "tool_calls": 1,
                "steps": [{"tool": "run_sql", "input": {"query": "SELECT 1;"}, "output": "1"}]}

    monkeypatch.setattr(agent_client, "chat", fake_chat)
    r = client.post("/ui/chat", data={"message": "なぜ", "store": NAME})
    assert r.status_code == 200
    j = r.json()
    assert j["message"] == "回答テスト" and j["tool_calls"] == 1
    assert j["steps"][0]["input"]["query"] == "SELECT 1;"


def test_chat_forwards_history(client: TestClient, monkeypatch):
    captured = {}

    async def fake_chat(message, *, context=None, history=None, include_steps=True, max_new_tokens=1024):
        captured["history"] = history
        return {"message": "ok", "tool_calls": 0, "steps": []}

    monkeypatch.setattr(agent_client, "chat", fake_chat)
    hist = [{"role": "user", "text": "前の質問"}, {"role": "assistant", "text": "前の回答"}]
    r = client.post("/ui/chat", data={"message": "続き", "history": json.dumps(hist)})
    assert r.status_code == 200
    assert captured["history"] == hist   # session memory is forwarded to the agent
