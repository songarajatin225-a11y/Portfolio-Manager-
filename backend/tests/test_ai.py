"""Claude integration with a scripted fake client: tool loop, grounding sources, refusals and reports."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from app.ai import client as ai_client
from app.ai.assistant import SYSTEM_PROMPT
from app.ai.reports import REPORT_SCHEMA
from tests.test_market_api import setup_simulated


def msg(content: list, stop: str = "end_turn", model: str = "claude-opus-5-5") -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop,
        model=model,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80),
    )


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def tool_use(name: str, args: dict, id_: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


class FakeMessages:
    def __init__(self, script: list) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(json.loads(json.dumps(kwargs, default=lambda o: o.__dict__)))
        step = self.script.pop(0)
        return step(kwargs) if callable(step) else step


@pytest.fixture
def fake_claude():
    def install(script: list) -> FakeMessages:
        fm = FakeMessages(script)
        ai_client.set_client(SimpleNamespace(beta=SimpleNamespace(messages=fm)))
        return fm

    yield install
    ai_client.set_client(None)


async def test_ai_unavailable_without_key(admin_client: AsyncClient) -> None:
    status = (await admin_client.get("/api/v1/ai/status")).json()
    assert status["configured"] is False
    r = await admin_client.post("/api/v1/ai/chat", json={"message": "hello"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_unavailable"


async def test_chat_tool_loop_grounding_and_history(admin_client: AsyncClient, fake_claude) -> None:
    await setup_simulated(admin_client)
    fm = fake_claude(
        [
            msg(
                [
                    SimpleNamespace(type="thinking", thinking="", signature="sig"),
                    tool_use("get_quote", {"symbol": "SIMBANKA"}),
                    tool_use("get_fundamentals", {"symbol": "NOPE"}, "tu_2"),
                ],
                stop="tool_use",
            ),
            msg([text("SIMBANKA has no live quote yet; fundamentals are unavailable.")]),
            msg([text("Follow-up answer.")]),
        ]
    )
    r = await admin_client.post("/api/v1/ai/chat", json={"message": "How is SIMBANKA doing?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"].startswith("SIMBANKA") and body["refused"] is False
    assert [c["tool"] for c in body["tool_calls"]] == ["get_quote", "get_fundamentals"]
    assert body["tool_calls"][1]["is_error"] is True  # unknown symbol surfaces as a tool error, not a crash
    assert body["usage"]["input_tokens"] == 200

    first, second = fm.calls
    assert first["model"] == "claude-opus-5-5"
    assert first["betas"] == ["server-side-fallback-2026-07-01"] and first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"}
    assert first["system"] == SYSTEM_PROMPT == second["system"]  # stable prefix
    assert first["tools"] == second["tools"] and all(t["strict"] for t in first["tools"])
    assert [t["name"] for t in first["tools"]] == sorted(t["name"] for t in first["tools"])
    assert "[Context: current time" in first["messages"][-1]["content"]
    assert "data mode: simulated" in first["messages"][-1]["content"]
    # assistant turn echoed unchanged (thinking block included), then tool results in one user message
    assert [b["type"] for b in second["messages"][1]["content"]] == ["thinking", "tool_use", "tool_use"]
    results = second["messages"][2]["content"]
    assert [x["tool_use_id"] for x in results] == ["tu_1", "tu_2"]
    assert results[1]["is_error"] is True and "not found" in results[1]["content"]
    assert json.loads(results[0]["content"])["data"]["symbol"] == "SIMBANKA"

    conv_id = body["conversation_id"]
    r2 = await admin_client.post("/api/v1/ai/chat", json={"message": "And now?", "conversation_id": conv_id})
    assert r2.json()["answer"] == "Follow-up answer."
    replay = fm.calls[2]["messages"]
    assert [m["role"] for m in replay] == ["user", "assistant", "user"]
    assert replay[0]["content"] == "How is SIMBANKA doing?" and isinstance(replay[1]["content"], str)
    conv = (await admin_client.get(f"/api/v1/ai/conversations/{conv_id}")).json()
    assert len(conv["messages"]) == 4


async def test_refusal_and_model_specific_fallbacks(admin_client: AsyncClient, fake_claude) -> None:
    refused = msg([], stop="refusal")
    refused.stop_details = SimpleNamespace(category="cyber")
    fake_claude([refused])
    body = (await admin_client.post("/api/v1/ai/chat", json={"message": "x"})).json()
    assert body["refused"] is True and "cyber" in body["answer"]

    await admin_client.put("/api/v1/admin/settings/ai", json={"value": {"model": "claude-haiku-5-5"}})
    fm = fake_claude([msg([text("ok")], model="claude-haiku-5-5")])
    await admin_client.post("/api/v1/ai/chat", json={"message": "y"})
    assert "fallbacks" not in fm.calls[0] and "betas" not in fm.calls[0]  # Haiku has no server-side fallback

    await admin_client.put("/api/v1/admin/settings/ai", json={"value": {"enabled": False}})
    assert (await admin_client.post("/api/v1/ai/chat", json={"message": "z"})).status_code == 403


def _report_json(**overrides: Any) -> str:
    content = {k: "text" for k in REPORT_SCHEMA["required"]}
    content.update(
        bullish_factors=["a"],
        bearish_factors=["b"],
        risk_factors=["c"],
        confidence={"level": "low", "rationale": "simulated data"},
        data_quality={"assessment": "partial", "missing_data": ["fundamentals"]},
    )
    content.update(overrides)
    return json.dumps(content)


async def test_research_report_structured_output(admin_client: AsyncClient, fake_claude) -> None:
    await setup_simulated(admin_client)
    fm = fake_claude([msg([text(_report_json())])])
    r = await admin_client.post("/api/v1/ai/reports", json={"symbol": "SIMBANKA"})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["content"]["symbol"] == "SIMBANKA" and "not investment advice" in rep["content"]["disclaimer"]
    assert rep["data_quality"]["datasets"]["fundamentals"] == "missing" and rep["data_quality"]["simulated"] is True
    call = fm.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "high"
    assert "DATA BUNDLE" in call["messages"][0]["content"] and "tools" not in call
    listing = (await admin_client.get("/api/v1/ai/reports")).json()
    assert listing[0]["symbol"] == "SIMBANKA"

    bad = json.loads(_report_json())
    del bad["valuation_analysis"]
    fake_claude([msg([text(json.dumps(bad))])])
    r = await admin_client.post("/api/v1/ai/reports", json={"symbol": "SIMBANKA"})
    assert r.status_code == 400 and "valuation_analysis" in r.json()["error"]["message"]

    summary = {
        "headline": "h",
        "summary": "s",
        "key_points": ["k"],
        "sector_view": "v",
        "risks": ["r"],
        "data_caveats": ["simulated"],
    }
    fake_claude([msg([text(json.dumps(summary))])])
    r = await admin_client.post("/api/v1/ai/market-summary")
    assert r.status_code == 201
    latest = (await admin_client.get("/api/v1/ai/market-summary/latest")).json()
    assert latest["content"]["headline"] == "h"


async def test_claude_news_sentiment_when_enabled(admin_client: AsyncClient, fake_claude, db) -> None:
    import httpx

    from app.db.models import NewsArticle
    from app.news.ingest import ingest_all
    from tests.test_news import RSS

    await admin_client.post("/api/v1/news/sources", json={"name": "Wire", "url": "https://news.example.com/rss"})
    await admin_client.put("/api/v1/admin/settings/ai", json={"value": {"use_ai_news_sentiment": True}})

    def classify(kwargs: dict) -> SimpleNamespace:
        items = json.loads(kwargs["messages"][0]["content"])
        out = [
            {"id": it["id"], "label": "negative", "score": -0.4, "confidence": 0.9, "impact": "normal"} for it in items
        ]
        return msg([text(json.dumps({"items": out}))])

    fm = fake_claude([classify])
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=RSS.encode())))
    res = await ingest_all(db, http=http)
    assert res["added"] == 3 and res["ai_classified"] == 3
    assert fm.calls[0]["output_config"]["effort"] == "low"
    rows = (await db.execute(__import__("sqlalchemy").select(NewsArticle))).scalars().all()
    assert {a.sentiment_method for a in rows} == {"claude:claude-opus-5-5"}
    assert {a.sentiment_label for a in rows} == {"negative"}
