"""WebSocket streaming against local mock servers, and the feed manager's reconnect behaviour."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import websockets

from app.core.bus import CH_TICKS, K_QUOTE, MemoryBus
from app.marketdata import manager as manager_mod
from app.marketdata.angelone import AngelOneProvider
from app.marketdata.base import MarketDataProvider, ProviderSettings
from app.marketdata.custom import CustomWebSocketProvider
from app.marketdata.manager import FeedManager
from app.marketdata.types import Quote
from app.marketdata.upstox import UpstoxProvider
from tests.test_marketdata_parsers import build_angel_snap

FIXTURE = (Path(__file__).parent / "fixtures" / "upstox_feed_full.bin").read_bytes()


async def _first(gen: AsyncIterator, n: int = 1, limit_s: float = 5.0) -> list:
    out = []

    async def run() -> None:
        async for item in gen:
            out.append(item)
            if len(out) >= n:
                break

    await asyncio.wait_for(run(), limit_s)
    await gen.aclose()
    return out


async def test_upstox_stream_subscribes_with_binary_frame_and_decodes_protobuf() -> None:
    received = {}

    async def server(ws) -> None:  # noqa: ANN001
        received["auth"] = ws.request.headers.get("Authorization")
        msg = await ws.recv()
        received["frame_is_binary"] = isinstance(msg, bytes)
        received["sub"] = json.loads(msg)
        await ws.send(FIXTURE)
        await asyncio.sleep(1)

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        # authorize endpoint unavailable -> provider falls back to the configured ws_url with a bearer header
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        p = UpstoxProvider(
            ProviderSettings(
                "upstox", "u", ws_url=f"ws://127.0.0.1:{port}", feed_type="full", options={"rate_limit_per_sec": 1000}
            ),
            {"access_token": "tok"},
            http=http,
        )
        batches = await _first(p.stream(["NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50"]))
    assert received["auth"] == "Bearer tok"
    assert received["frame_is_binary"] is True
    assert received["sub"]["method"] == "sub" and received["sub"]["data"]["mode"] == "full"
    assert received["sub"]["data"]["instrumentKeys"] == ["NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50"]
    assert {q.provider_key for q in batches[0]} >= {"NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50"}


async def test_angel_stream_headers_subscription_and_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.marketdata import angelone

    monkeypatch.setattr(angelone, "HEARTBEAT_SECONDS", 0.05)
    received: dict = {"pings": 0}

    async def server(ws) -> None:  # noqa: ANN001
        received["headers"] = {k.lower(): v for k, v in ws.request.headers.items()}
        received["sub"] = json.loads(await ws.recv())
        await ws.send(build_angel_snap("2885", 1))
        try:
            while True:
                if await ws.recv() == "ping":
                    received["pings"] += 1
                    await ws.send("pong")
        except websockets.ConnectionClosed:
            pass

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        creds = {
            "api_key": "AK",
            "client_code": "C1",
            "pin": "1",
            "totp_secret": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
            "jwt_token": "J",
            "feed_token": "F",
            "token_expires_at": "2999-01-01T00:00:00+00:00",
        }
        p = AngelOneProvider(ProviderSettings("angelone", "a", ws_url=f"ws://127.0.0.1:{port}"), creds)
        gen = p.stream(["NSE:2885", "BSE:500325"])
        first = await asyncio.wait_for(gen.__anext__(), 5)
        await asyncio.sleep(0.2)
        await gen.aclose()
    h = received["headers"]
    assert (h["authorization"], h["x-api-key"], h["x-client-code"], h["x-feed-token"]) == ("J", "AK", "C1", "F")
    assert received["sub"]["action"] == 1 and received["sub"]["params"]["mode"] == 3
    assert {"exchangeType": 1, "tokens": ["2885"]} in received["sub"]["params"]["tokenList"]
    assert {"exchangeType": 3, "tokens": ["500325"]} in received["sub"]["params"]["tokenList"]
    assert first[0].provider_key == "NSE:2885" and first[0].ltp == pytest.approx(1523.45)
    assert received["pings"] >= 1


async def test_custom_ws_provider_templating_and_mapping() -> None:
    got = {}

    async def server(ws) -> None:  # noqa: ANN001
        got["path"] = ws.request.path
        got["sub"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"data": [{"s": "AAA", "p": 101.0, "pc": 100.0}]}))
        await asyncio.sleep(1)

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        opts = {
            "subscribe_message": {"op": "sub", "args": "{keys_list}", "csv": "{keys}"},
            "ws_items_path": "data",
            "quote_map": {"key": "s", "ltp": "p", "prev_close": "pc"},
            "ws_auth_query": "token",
        }
        p = CustomWebSocketProvider(
            ProviderSettings("custom_ws", "v", ws_url=f"ws://127.0.0.1:{port}/feed", options=opts), {"api_key": "TK"}
        )
        batches = await _first(p.stream(["AAA", "BBB"]))
    assert got["path"] == "/feed?token=TK"
    assert got["sub"] == {"op": "sub", "args": ["AAA", "BBB"], "csv": "AAA,BBB"}
    assert batches[0][0].provider_key == "AAA" and batches[0][0].change_pct == pytest.approx(1.0)


class FlakyProvider(MarketDataProvider):
    """Stream fails twice, then delivers ticks."""

    provider_type = "flaky"
    display_name = "Flaky"
    supports_streaming = True

    def __init__(self) -> None:
        super().__init__(ProviderSettings("flaky", "flaky"), {})
        self.connects = 0

    async def authenticate(self) -> None:
        return None

    async def get_quotes(self, provider_keys):  # noqa: ANN001, ANN201
        return []

    async def get_candles(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return []

    async def stream(self, provider_keys):  # noqa: ANN001, ANN201
        self.connects += 1
        if self.connects <= 2:
            raise ConnectionResetError("socket dropped")
        while True:
            now = datetime.now(UTC)
            yield [
                Quote(provider_key="K1", ltp=100.0, prev_close=99.0, received_at=now, exchange_ts=now, source="flaky")
            ]
            await asyncio.sleep(0.01)


async def test_feed_manager_reconnects_validates_merges_and_publishes(
    bus: MemoryBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager_mod, "backoff_delays", lambda *a, **k: iter([0.0] * 100))
    fm = FeedManager()
    fm.provider = FlakyProvider()
    fm.key_to_id = {"K1": 7, "K2": 8}
    fm.meta = {7: ("SYM", "NSE"), 8: ("BAD", "NSE")}
    sub = bus.subscribe(CH_TICKS)
    listener = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)
    task = asyncio.create_task(fm._stream_loop(["K1"]))
    _, msg = await asyncio.wait_for(listener, 5)
    task.cancel()
    assert fm.provider.connects == 3
    assert fm.health["reconnects"] == 2 and fm.health["state"] == "connected"
    tick = msg["quotes"][0]
    assert tick["instrument_id"] == 7 and tick["symbol"] == "SYM" and tick["change_pct"] == pytest.approx(1.0101, 1e-3)
    cached = await bus.get_json(f"{K_QUOTE}7")
    assert cached["ltp"] == 100.0

    # partial update merges with the previous snapshot; invalid quotes are rejected and counted
    now = datetime.now(UTC)
    accepted = await fm.process(
        [
            Quote(provider_key="K1", ltp=101.0, received_at=now, source="flaky"),
            Quote(provider_key="K2", ltp=-1, received_at=now, source="flaky"),
            Quote(provider_key="UNKNOWN", ltp=5, received_at=now, source="flaky"),
        ]
    )
    assert accepted == 1
    merged = fm.last[7]
    assert merged.prev_close == 99.0 and merged.change == pytest.approx(2.0)
    assert fm.health["invalid_quotes"] == 1 and fm.health["last_invalid"]["symbol"] == "BAD"
    await sub.aclose()
