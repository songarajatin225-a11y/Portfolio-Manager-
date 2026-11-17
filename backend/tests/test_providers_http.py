"""Provider REST behaviour against mocked HTTP transports (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote

import httpx
import pytest

from app.marketdata.angelone import AngelOneProvider
from app.marketdata.base import AuthenticationRequired, ProviderSettings
from app.marketdata.custom import CustomRestProvider
from app.marketdata.upstox import UpstoxProvider


def upstox(handler, **creds) -> UpstoxProvider:  # noqa: ANN001
    creds = {
        "api_key": "client123",
        "api_secret": "s3cret",
        "redirect_uri": "https://app.example/cb",
        "access_token": "tok",
        **creds,
    }
    return UpstoxProvider(
        ProviderSettings("upstox", "u", options={"rate_limit_per_sec": 1000}),
        creds,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_upstox_quotes_request_shape() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["auth"] = req.headers["authorization"]
        seen["keys"] = parse_qs(req.url.query.decode())["instrument_key"][0]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_EQ:RELIANCE": {
                        "instrument_token": "NSE_EQ|INE002A01018",
                        "last_price": 2940.0,
                        "prev_close_price": 2920.0,
                        "ohlc": {"open": 2900, "high": 2950, "low": 2890},
                    }
                },
            },
        )

    p = upstox(handler)
    quotes = await p.get_quotes(["NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50"])
    assert seen == {
        "path": "/v3/market-quote/quotes",
        "auth": "Bearer tok",
        "keys": "NSE_EQ|INE002A01018,NSE_INDEX|Nifty 50",
    }
    assert quotes[0].provider_key == "NSE_EQ|INE002A01018" and quotes[0].change == pytest.approx(20.0)


async def test_upstox_candles_chunking_encoding_and_order() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        raw = req.url.raw_path.decode()
        calls.append(raw)
        parts = unquote(raw).split("/")
        if "intraday" in parts:
            rows = []
        else:
            to_d = parts[-2]
            rows = [
                [f"{to_d}T15:29:00+05:30", 10, 11, 9, 10.5, 100, 0],
                [f"{to_d}T09:15:00+05:30", 10, 10, 10, 10, 50, 0],
            ]
        return httpx.Response(200, json={"status": "success", "data": {"candles": rows}})

    p = upstox(handler)
    end = datetime.now(UTC)
    start = end - timedelta(days=60)
    bars = await p.get_candles("NSE_EQ|INE002A01018", "1m", start, end)
    hist = [c for c in calls if "intraday" not in c]
    assert len(hist) == 3  # 60 days in <=28-day chunks
    assert all(c.startswith("/v3/historical-candle/NSE_EQ%7CINE002A01018/minutes/1/") for c in hist)
    assert any("intraday" in c for c in calls)
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)


async def test_upstox_auth_errors_and_retry() -> None:
    p = upstox(lambda r: httpx.Response(200, json={}), access_token="")
    with pytest.raises(AuthenticationRequired):
        await p.authenticate()
    p401 = upstox(lambda r: httpx.Response(401, json={"status": "error"}))
    with pytest.raises(AuthenticationRequired):
        await p401.get_quotes(["NSE_EQ|X"])

    attempts = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"status": "success", "data": {}})

    assert await upstox(flaky).get_quotes(["NSE_EQ|X"]) == []
    assert attempts["n"] == 2

    expired = upstox(
        lambda r: httpx.Response(200, json={}), token_expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    )
    with pytest.raises(AuthenticationRequired, match="expired"):
        await expired.authenticate()


async def test_upstox_oauth_code_exchange() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["form"] = parse_qs(req.content.decode())
        return httpx.Response(200, json={"access_token": "new-token", "user_id": "AB1234"})

    p = upstox(handler, access_token="")
    url = p.authorization_url("STATE")
    assert url.startswith("https://api.upstox.com/v2/login/authorization/dialog?")
    assert "client_id=client123" in url and "state=STATE" in url
    await p.exchange_code("CODE")
    assert seen["path"] == "/v2/login/authorization/token"
    assert seen["form"]["grant_type"] == ["authorization_code"] and seen["form"]["code"] == ["CODE"]
    assert p.updated_credentials and p.updated_credentials["access_token"] == "new-token"
    assert p.token_expires_at and p.token_expires_at > datetime.now(UTC)


def angel(handler, **creds) -> AngelOneProvider:  # noqa: ANN001
    creds = {
        "api_key": "AKEY",
        "client_code": "C123",
        "pin": "1234",
        "totp_secret": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
        **creds,
    }
    opts = {
        "rate_limit_per_sec": 1000,
        "client_local_ip": "10.0.0.2",
        "client_public_ip": "1.2.3.4",
        "mac_address": "aa:bb:cc:dd:ee:ff",
    }
    return AngelOneProvider(
        ProviderSettings("angelone", "a", options=opts),
        creds,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_angel_login_quotes_and_candles() -> None:
    log: list[tuple[str, dict, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        log.append((req.url.path, dict(req.headers), body))
        if req.url.path.endswith("loginByPassword"):
            return httpx.Response(
                200, json={"status": True, "data": {"jwtToken": "JWT", "refreshToken": "RT", "feedToken": "FT"}}
            )
        if req.url.path.endswith("/market/v1/quote/"):
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "data": {
                        "fetched": [
                            {
                                "exchange": "NSE",
                                "tradingSymbol": "RELIANCE-EQ",
                                "symbolToken": "2885",
                                "ltp": 2940.0,
                                "open": 2900,
                                "high": 2950,
                                "low": 2890,
                                "close": 2920.0,
                                "netChange": 20.0,
                                "percentChange": 0.68,
                                "avgPrice": 2925.3,
                                "tradeVolume": 1234567,
                                "exchFeedTime": "17-Nov-2026 10:46:10",
                                "depth": {"buy": [{"price": 2939.9, "quantity": 10, "orders": 2}], "sell": []},
                            }
                        ],
                        "unfetched": [],
                    },
                },
            )
        if req.url.path.endswith("getCandleData"):
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "data": [
                        ["2026-11-16T00:00:00+05:30", 10, 11, 9, 10.5, 1000],
                        ["2026-11-13T00:00:00+05:30", 9, 10, 8, 9.5, 900],
                    ],
                },
            )
        return httpx.Response(404)

    p = angel(handler)
    await p.authenticate()
    path, headers, body = log[0]
    assert path == "/rest/auth/angelbroking/user/v1/loginByPassword"
    assert headers["x-privatekey"] == "AKEY" and headers["x-usertype"] == "USER" and headers["x-sourceid"] == "WEB"
    assert headers["x-clientpublicip"] == "1.2.3.4" and headers["x-macaddress"] == "aa:bb:cc:dd:ee:ff"
    assert body["clientcode"] == "C123" and body["password"] == "1234" and len(body["totp"]) == 6
    assert p.updated_credentials and p.updated_credentials["feed_token"] == "FT"

    quotes = await p.get_quotes(["NSE:2885"])
    _, qh, qb = log[-1]
    assert qh["authorization"] == "Bearer JWT" and qb == {"mode": "FULL", "exchangeTokens": {"NSE": ["2885"]}}
    q = quotes[0]
    assert q.provider_key == "NSE:2885" and q.prev_close == 2920.0 and q.change_pct == 0.68 and q.bid == 2939.9
    assert q.exchange_ts is not None and q.exchange_ts.utcoffset() is not None

    bars = await p.get_candles("NSE:2885", "1d", datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 17, tzinfo=UTC))
    _, _, cb = log[-1]
    assert cb["interval"] == "ONE_DAY" and cb["symboltoken"] == "2885" and cb["exchange"] == "NSE"
    assert [b.close for b in bars] == [9.5, 10.5]  # sorted ascending


async def test_angel_reuses_valid_session_and_reports_bad_totp() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": True, "data": {}})

    valid = angel(
        handler, jwt_token="J", feed_token="F", token_expires_at=(datetime.now(UTC) + timedelta(hours=5)).isoformat()
    )
    await valid.authenticate()
    assert calls["n"] == 0
    bad = angel(handler, totp_secret="not base32 !!")
    with pytest.raises(AuthenticationRequired, match="TOTP"):
        await bad.authenticate()


async def test_custom_rest_provider_mapping() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-api-key"] == "K"
        if req.url.path == "/v1/quotes":
            assert parse_qs(req.url.query.decode())["s"] == ["AAA,BBB"]
            return httpx.Response(
                200,
                json={
                    "result": [{"sym": "AAA", "px": {"last": 10.5, "prev": 10.0}}, {"sym": "BBB", "px": {"last": 0}}]
                },
            )
        return httpx.Response(200, json={"rows": [[1763337600, 1, 2, 0.5, 1.5, 100]]})

    opts = {
        "quote_path": "/v1/quotes?s={keys}",
        "quote_items_path": "result",
        "quote_map": {"key": "sym", "ltp": "px.last", "prev_close": "px.prev"},
        "candles_path": "/v1/candles/{key}?i={interval}",
        "candles_items_path": "rows",
        "auth_header": "X-API-Key",
        "rate_limit_per_sec": 1000,
    }
    p = CustomRestProvider(
        ProviderSettings("custom_rest", "vendor", rest_url="https://vendor.example", options=opts),
        {"api_key": "K"},
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    quotes = await p.get_quotes(["AAA", "BBB"])
    assert len(quotes) == 1 and quotes[0].change_pct == pytest.approx(5.0) and quotes[0].source == "custom:vendor"
    bars = await p.get_candles("AAA", "1d", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert bars[0].close == 1.5 and bars[0].ts == datetime.fromtimestamp(1763337600, UTC)
