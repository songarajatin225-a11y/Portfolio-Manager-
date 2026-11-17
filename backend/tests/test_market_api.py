"""Provider admin API, instrument sync, candles, quote labelling and data-mode isolation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.core.bus import K_QUOTE, MemoryBus
from app.core.config import get_settings
from app.marketdata.base import ProviderSettings
from app.marketdata.manager import FeedManager
from app.marketdata.simulated import SimulatedProvider
from app.services.market_data import label_quote
from tests.conftest import register_and_login


async def setup_simulated(client: AsyncClient) -> int:
    r = await client.post("/api/v1/admin/providers", json={"name": "Sim", "provider_type": "simulated"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert (await client.post(f"/api/v1/admin/providers/{pid}/activate")).status_code == 200
    r = await client.post(f"/api/v1/admin/providers/{pid}/sync-instruments")
    assert r.status_code == 202
    for _ in range(100):
        status = (await client.get("/api/v1/admin/providers/sync-status")).json()
        if status["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert status["state"] == "completed", status
    return pid


async def test_provider_crud_masks_credentials(admin_client: AsyncClient) -> None:
    types = {t["type"] for t in (await admin_client.get("/api/v1/admin/providers/types")).json()}
    assert {"upstox", "angelone", "custom_rest", "custom_ws", "simulated"} <= types

    r = await admin_client.post(
        "/api/v1/admin/providers",
        json={
            "name": "My Upstox",
            "provider_type": "upstox",
            "credentials": {"api_key": "client-123", "api_secret": "very-secret-abcd", "redirect_uri": "https://x/cb"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["rest_url"] == "https://api.upstox.com"
    assert body["credentials"]["api_secret"] == "••••abcd"
    assert body["credentials"]["api_key"] == "client-123"  # non-secret identifier shown as-is
    assert "very-secret-abcd" not in r.text
    pid = body["id"]

    listing = await admin_client.get("/api/v1/admin/providers")
    assert "very-secret-abcd" not in listing.text

    r = await admin_client.patch(f"/api/v1/admin/providers/{pid}", json={"credentials": {"api_secret": ""}})
    assert r.json()["credentials_set"]["api_secret"] is False
    r = await admin_client.patch(f"/api/v1/admin/providers/{pid}", json={"credentials": {"bogus": "x"}})
    assert r.status_code == 422

    # Test Connection without a token reports the actionable auth problem
    r = await admin_client.post(f"/api/v1/admin/providers/{pid}/test")
    assert r.json()["ok"] is False and "Authentication required" in r.json()["message"]

    url = await admin_client.get(f"/api/v1/admin/providers/{pid}/oauth-url")
    assert url.status_code == 200 and "authorization/dialog" in url.json()["url"]

    r = await admin_client.post(
        "/api/v1/admin/providers", json={"name": "Bad", "provider_type": "custom_rest", "rest_url": "ftp://nope"}
    )
    assert r.status_code == 422
    assert (await admin_client.delete(f"/api/v1/admin/providers/{pid}")).status_code == 204


async def test_simulated_end_to_end(admin_client: AsyncClient, bus: MemoryBus) -> None:
    pid = await setup_simulated(admin_client)
    test = (await admin_client.post(f"/api/v1/admin/providers/{pid}/test")).json()
    assert test["ok"] is True and test["details"]["sample"][0]["key"] == "SIM:SIMNIFTY50"

    results = (await admin_client.get("/api/v1/instruments/search?q=SIMBANK")).json()
    assert results and all(r["is_simulated"] for r in results)
    assert next(r for r in results if r["symbol"] == "SIMBANKA")["sector"] == "Banking"

    candles = (await admin_client.get("/api/v1/stocks/SIMBANKA/candles?interval=1d")).json()
    assert candles["data_status"] == "SIMULATED" and candles["fetched_from_provider"] is True
    assert len(candles["candles"]) > 300
    c0 = candles["candles"][0]
    assert c0["low"] <= min(c0["open"], c0["close"]) and c0["high"] >= max(c0["open"], c0["close"])
    # second request is served from storage
    again = (await admin_client.get("/api/v1/stocks/SIMBANKA/candles?interval=1d")).json()
    assert again["fetched_from_provider"] is False and len(again["candles"]) == len(candles["candles"])
    weekly = (await admin_client.get("/api/v1/stocks/SIMBANKA/candles?interval=1w")).json()
    assert 0 < len(weekly["candles"]) < len(candles["candles"])

    indices = (await admin_client.get("/api/v1/market/indices")).json()
    assert indices["data_mode"] == "simulated"
    assert all(i["instrument"] is not None for i in indices["items"])

    # push simulated ticks through the feed manager and read them back with labels
    fm = FeedManager()
    fm.provider = SimulatedProvider(ProviderSettings("simulated", "Sim"), {})
    inst = (await admin_client.get("/api/v1/instruments/SIMBANKA")).json()["instrument"]
    fm.key_to_id = {"SIM:SIMBANKA": inst["id"]}
    fm.meta = {inst["id"]: ("SIMBANKA", "NSE")}
    assert await fm.process(await fm.provider.get_quotes(["SIM:SIMBANKA"])) == 1
    q = (await admin_client.get(f"/api/v1/market/quotes?ids={inst['id']}")).json()["quotes"][str(inst["id"])]
    assert q["data_status"] == "SIMULATED" and q["is_simulated"] is True and q["source"] == "simulated"

    live = (await admin_client.get("/api/v1/market/live?scope=universe")).json()
    assert live["data_mode"] == "simulated" and any(i["quote"] for i in live["items"])

    # switching to a real provider hides every simulated instrument and quote
    real = await admin_client.post("/api/v1/admin/providers", json={"name": "Real", "provider_type": "angelone"})
    await admin_client.post(f"/api/v1/admin/providers/{real.json()['id']}/activate")
    assert (await admin_client.get("/api/v1/instruments/search?q=SIMBANK")).json() == []
    assert (await admin_client.get(f"/api/v1/market/quotes?ids={inst['id']}")).json()["quotes"] == {}
    assert (await admin_client.get("/api/v1/stocks/SIMBANKA/candles")).status_code == 404


async def test_simulated_provider_blocked_when_disabled(admin_client: AsyncClient) -> None:
    settings = get_settings()
    settings.allow_simulated_feed = False
    try:
        r = await admin_client.post("/api/v1/admin/providers", json={"name": "Sim", "provider_type": "simulated"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "simulated_disabled"
        types = {t["type"] for t in (await admin_client.get("/api/v1/admin/providers/types")).json()}
        assert "simulated" not in types
    finally:
        settings.allow_simulated_feed = True


async def test_provider_admin_requires_admin(client: AsyncClient) -> None:
    await register_and_login(client)
    await client.post(
        "/api/v1/users", json={"email": "an@example.com", "password": "AnalystPass123", "role": "analyst"}
    )
    client.cookies.clear()
    r = await client.post("/api/v1/auth/login", json={"email": "an@example.com", "password": "AnalystPass123"})
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert (await client.get("/api/v1/admin/providers")).status_code == 403
    assert (await client.get("/api/v1/market/status")).status_code == 200


async def test_holidays_api(admin_client: AsyncClient) -> None:
    r = await admin_client.post("/api/v1/market/holidays", json={"date": "2026-12-25", "description": "Christmas"})
    assert r.status_code == 201
    listed = (await admin_client.get("/api/v1/market/holidays?year=2026")).json()
    assert listed[0]["description"] == "Christmas" and listed[0]["source"] == "manual"


def test_quote_labels() -> None:
    now = datetime.now(UTC)
    fresh = {"received_at": now.isoformat(), "is_simulated": False}
    old = {"received_at": (now - timedelta(seconds=120)).isoformat(), "is_simulated": False}
    assert label_quote(fresh, market_state="open", stale_after=30, now=now)["data_status"] == "LIVE"
    stale = label_quote(old, market_state="open", stale_after=30, now=now)
    assert stale["data_status"] == "DELAYED" and stale["is_stale"] is True
    assert label_quote(old, market_state="closed", stale_after=30, now=now)["data_status"] == "HISTORICAL"
    assert (
        label_quote({**fresh, "provider_delayed": True}, market_state="open", stale_after=30, now=now)["data_status"]
        == "DELAYED"
    )
    assert (
        label_quote({**fresh, "is_simulated": True}, market_state="closed", stale_after=30, now=now)["data_status"]
        == "SIMULATED"
    )


async def test_quotes_cache_keyed_by_instrument(bus: MemoryBus) -> None:
    await bus.set_json(f"{K_QUOTE}1", {"ltp": 1})
    assert (await bus.get_json(f"{K_QUOTE}1"))["ltp"] == 1


async def test_watchlists_crud_and_isolation(admin_client: AsyncClient) -> None:
    await setup_simulated(admin_client)
    wl = (await admin_client.post("/api/v1/watchlists", json={"name": "Banks"})).json()
    assert (await admin_client.post("/api/v1/watchlists", json={"name": "Banks"})).status_code == 409
    r = await admin_client.post(f"/api/v1/watchlists/{wl['id']}/items", json={"symbol": "SIMBANKA"})
    assert r.status_code == 201
    assert (
        await admin_client.post(f"/api/v1/watchlists/{wl['id']}/items", json={"symbol": "SIMBANKA"})
    ).status_code == 409
    assert (await admin_client.post(f"/api/v1/watchlists/{wl['id']}/items", json={"symbol": "NOPE"})).status_code == 404
    detail = (await admin_client.get(f"/api/v1/watchlists/{wl['id']}")).json()
    assert [i["instrument"]["symbol"] for i in detail["items"]] == ["SIMBANKA"]
    listing = (await admin_client.get("/api/v1/watchlists")).json()
    assert listing[0]["item_count"] == 1
    live = (await admin_client.get(f"/api/v1/market/live?scope=watchlist:{wl['id']}")).json()
    assert len(live["items"]) == 1

    # another user cannot see or modify it
    await admin_client.post("/api/v1/users", json={"email": "b@example.com", "password": "AnalystPass123"})
    admin_client.cookies.clear()
    r = await admin_client.post("/api/v1/auth/login", json={"email": "b@example.com", "password": "AnalystPass123"})
    admin_client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert (await admin_client.get(f"/api/v1/watchlists/{wl['id']}")).status_code == 404
    assert (await admin_client.delete(f"/api/v1/watchlists/{wl['id']}")).status_code == 404
    assert (await admin_client.get("/api/v1/watchlists")).json() == []
