"""News sentiment, URL canonicalisation, de-duplication, entity tagging and the news API."""

from __future__ import annotations

import httpx
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import NewsSource
from app.news.ingest import canonical_url, ingest_all
from app.news.sentiment import classify
from tests.test_market_api import setup_simulated

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Test wire</title>
<item><title>Simulated Bank Alpha Q2 profit surges 40%, beats estimates</title>
<link>https://news.example.com/a?utm_source=rss&amp;id=1</link><pubDate>Mon, 16 Nov 2026 09:30:00 +0530</pubDate>
<description>&lt;p&gt;Net interest income grew strongly.&lt;/p&gt;</description></item>
<item><title>Simulated Steel Alpha shares plunge after SEBI probe</title>
<link>https://news.example.com/b</link><pubDate>Mon, 16 Nov 2026 11:00:00 +0530</pubDate></item>
<item><title>Markets close flat ahead of policy meeting</title>
<link>https://news.example.com/c</link><pubDate>Mon, 16 Nov 2026 15:45:00 +0530</pubDate></item>
</channel></rss>"""

RSS_SYNDICATED = RSS.replace("news.example.com", "other.example.org")


def test_sentiment_classifier() -> None:
    pos = classify("Company X profit surges 40%, beats estimates")
    assert pos.label == "positive" and pos.impact == "high" and pos.score > 0
    neg = classify("Shares plunge after SEBI probe into fraud")
    assert neg.label == "negative" and neg.impact == "high"
    assert classify("Board meeting scheduled for Tuesday").label == "neutral"
    assert classify("Margins not strong this quarter").score < 0  # negation flips
    mixed = classify("Profit rises but revenue falls")
    assert mixed.label == "neutral"  # balanced evidence is not forced into a label


def test_canonical_url_strips_tracking() -> None:
    assert canonical_url("https://News.Example.com/a/?utm_source=x&id=1#frag") == "https://news.example.com/a?id=1"


async def test_ingestion_dedupe_tagging_and_api(admin_client: AsyncClient, db) -> None:
    await setup_simulated(admin_client)
    for name, url in (
        ("Wire", "https://news.example.com/rss"),
        ("Syndicator", "https://other.example.org/rss"),
        ("Broken", "https://broken.example.net/rss"),
    ):
        r = await admin_client.post("/api/v1/news/sources", json={"name": name, "url": url})
        assert r.status_code == 201
    assert (
        await admin_client.post("/api/v1/news/sources", json={"name": "dup", "url": "https://news.example.com/rss"})
    ).status_code == 409
    calls = {"etag_seen": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "broken.example.net":
            return httpx.Response(500)
        if req.headers.get("if-none-match") == '"v1"':
            calls["etag_seen"] = True
            return httpx.Response(304)
        body = RSS if req.url.host == "news.example.com" else RSS_SYNDICATED
        return httpx.Response(200, content=body.encode(), headers={"etag": '"v1"'})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = await ingest_all(db, http=http)
    assert res["added"] == 3  # syndicated copies with identical headlines are dropped
    assert res["errors"][0]["source"] == "Broken"
    again = await ingest_all(db, http=http)
    assert again["added"] == 0 and calls["etag_seen"]
    broken = await db.scalar(select(NewsSource).where(NewsSource.name == "Broken"))
    await db.refresh(broken)
    assert "HTTPStatusError" in broken.last_error

    news = (await admin_client.get("/api/v1/news?days=90")).json()
    assert news["total"] == 3
    by_title = {n["title"]: n for n in news["items"]}
    bank = by_title["Simulated Bank Alpha Q2 profit surges 40%, beats estimates"]
    assert bank["sentiment"] == "positive" and bank["impact"] == "high" and bank["symbols"] == ["SIMBANKA"]
    assert bank["url"] == "https://news.example.com/a?id=1" and bank["summary"] == "Net interest income grew strongly."
    steel = by_title["Simulated Steel Alpha shares plunge after SEBI probe"]
    assert steel["sentiment"] == "negative" and steel["symbols"] == ["SIMMETA"]
    only_bank = (await admin_client.get("/api/v1/news?days=90&symbol=SIMBANKA")).json()
    assert [n["title"] for n in only_bank["items"]] == [bank["title"]]
    negative = (await admin_client.get("/api/v1/news?days=90&sentiment=negative")).json()
    assert negative["total"] == 1
    srcs = (await admin_client.get("/api/v1/news/sources")).json()
    assert {s["name"] for s in srcs} == {"Wire", "Syndicator", "Broken"}
