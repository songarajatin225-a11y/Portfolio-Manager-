"""Upstox API v2/v3 provider.

Endpoints and payloads were checked against the official ``upstox-python-sdk`` 2.30.0. Access
tokens come from the OAuth2 authorisation-code flow and expire at about 03:30 IST every day;
Upstox issues no refresh token, so an administrator must reconnect once per trading day
(Data Provider Settings → Connect Upstox).
"""

from __future__ import annotations

import gzip
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import websockets

from app.marketdata import upstox_proto
from app.marketdata.base import (
    AuthenticationRequired,
    CredentialField,
    MarketDataProvider,
    NotSupported,
    ProviderError,
)
from app.marketdata.calendar import IST, next_upstox_token_expiry
from app.marketdata.http import request_with_retry
from app.marketdata.types import (
    CandleBar,
    DepthLevel,
    HolidayRecord,
    InstrumentRecord,
    MarketStatusInfo,
    Quote,
)

logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
QUOTE_BATCH = 500  # Upstox accepts up to 500 instrument keys per quote request

# our interval -> (v3 unit, v3 interval, max days per request)
INTERVAL_MAP: dict[str, tuple[str, str, int]] = {
    "1m": ("minutes", "1", 28),
    "5m": ("minutes", "5", 28),
    "15m": ("minutes", "15", 28),
    "30m": ("minutes", "30", 28),
    "1h": ("hours", "1", 85),
    "1d": ("days", "1", 3650),
    "1w": ("weeks", "1", 3650 * 3),
    "1mo": ("months", "1", 3650 * 3),
}

SEGMENTS = {
    "NSE_EQ": ("NSE", "EQ"),
    "BSE_EQ": ("BSE", "EQ"),
    "NSE_INDEX": ("NSE", "INDEX"),
    "BSE_INDEX": ("BSE", "INDEX"),
    "NSE_FO": ("NSE", "FO"),
    "BSE_FO": ("BSE", "FO"),
}

UPSTOX_STATUS_MAP = {
    "NORMAL_OPEN": "open",
    "NORMAL_CLOSE": "closed",
    "PRE_OPEN_START": "pre_open",
    "PRE_OPEN_END": "pre_open",
    "CLOSING_START": "post_close",
    "CLOSING_END": "closed",
}


def _ms(ts: int | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts / 1000, UTC)


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return _ms(int(value))
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=IST)
    except ValueError:
        return None


def feed_to_quotes(decoded: dict[str, Any], received_at: datetime, source: str = "upstox") -> list[Quote]:
    """Convert a decoded ``FeedResponse`` into quotes."""
    quotes: list[Quote] = []
    for key, feed in (decoded.get("feeds") or {}).items():
        if not feed:
            continue
        ltpc: dict[str, Any] | None = None
        q_kwargs: dict[str, Any] = {}
        full = feed.get("fullFeed") or {}
        if "marketFF" in full:
            mff = full["marketFF"]
            ltpc = mff.get("ltpc")
            levels = (mff.get("marketLevel") or {}).get("bidAskQuote") or []
            q_kwargs["depth_buy"] = [
                DepthLevel(lv.get("bidP", 0.0), float(lv.get("bidQ", 0))) for lv in levels if lv.get("bidP")
            ]
            q_kwargs["depth_sell"] = [
                DepthLevel(lv.get("askP", 0.0), float(lv.get("askQ", 0))) for lv in levels if lv.get("askP")
            ]
            q_kwargs["vwap"] = mff.get("atp") or None
            q_kwargs["volume"] = float(mff["vtt"]) if "vtt" in mff else None
            q_kwargs["oi"] = mff.get("oi") or None
            q_kwargs["total_buy_qty"] = mff.get("tbq")
            q_kwargs["total_sell_qty"] = mff.get("tsq")
            ohlc_list = (mff.get("marketOHLC") or {}).get("ohlc") or []
        elif "indexFF" in full:
            iff = full["indexFF"]
            ltpc = iff.get("ltpc")
            ohlc_list = (iff.get("marketOHLC") or {}).get("ohlc") or []
        elif "firstLevelWithGreeks" in feed:
            flg = feed["firstLevelWithGreeks"]
            ltpc = flg.get("ltpc")
            depth = flg.get("firstDepth") or {}
            if depth.get("bidP"):
                q_kwargs["depth_buy"] = [DepthLevel(depth["bidP"], float(depth.get("bidQ", 0)))]
            if depth.get("askP"):
                q_kwargs["depth_sell"] = [DepthLevel(depth["askP"], float(depth.get("askQ", 0)))]
            q_kwargs["volume"] = float(flg["vtt"]) if "vtt" in flg else None
            q_kwargs["oi"] = flg.get("oi") or None
            ohlc_list = []
        else:
            ltpc = feed.get("ltpc")
            ohlc_list = []
        if not ltpc or not ltpc.get("ltp"):
            continue
        day = next((o for o in ohlc_list if o.get("interval") == "1d"), None)
        if day:
            q_kwargs.update(open=day.get("open"), high=day.get("high"), low=day.get("low"))
            if q_kwargs.get("volume") is None and day.get("vol"):
                q_kwargs["volume"] = float(day["vol"])
        ltt = _ms(ltpc.get("ltt"))
        quotes.append(
            Quote(
                provider_key=key,
                ltp=float(ltpc["ltp"]),
                prev_close=ltpc.get("cp") or None,
                last_trade_time=ltt,
                exchange_ts=ltt or _ms(decoded.get("currentTs")),
                received_at=received_at,
                source=source,
                **q_kwargs,
            ).finalize()
        )
    return quotes


def market_info_to_status(decoded: dict[str, Any], exchange: str) -> MarketStatusInfo | None:
    seg = (decoded.get("marketInfo") or {}).get("segmentStatus") or {}
    raw = seg.get(f"{exchange}_EQ")
    if raw is None:
        return None
    name = upstox_proto.MARKET_STATUS.get(raw, str(raw))
    return MarketStatusInfo(
        exchange=exchange,
        status=UPSTOX_STATUS_MAP.get(name, "closed"),
        source="upstox_feed",
        as_of=_ms(decoded.get("currentTs")) or datetime.now(UTC),
        detail=name,
    )


def parse_rest_quote(item: dict[str, Any], received_at: datetime) -> Quote | None:
    key = item.get("instrument_token")
    ltp = item.get("last_price")
    if not key or not ltp:
        return None
    ohlc = item.get("ohlc") or {}
    depth = item.get("depth") or {}
    prev_close = item.get("prev_close_price")
    if not prev_close and item.get("net_change") is not None:
        prev_close = ltp - item["net_change"]
    return Quote(
        provider_key=key,
        ltp=float(ltp),
        open=ohlc.get("open") or None,
        high=ohlc.get("high") or None,
        low=ohlc.get("low") or None,
        prev_close=prev_close or None,
        change=item.get("net_change"),
        volume=float(item["volume"]) if item.get("volume") is not None else None,
        vwap=item.get("average_price") or None,
        oi=item.get("oi") or None,
        total_buy_qty=item.get("total_buy_quantity"),
        total_sell_qty=item.get("total_sell_quantity"),
        upper_circuit=item.get("upper_circuit_limit") or None,
        lower_circuit=item.get("lower_circuit_limit") or None,
        high_52w=item.get("year_high") or None,
        low_52w=item.get("year_low") or None,
        last_trade_time=_parse_dt(item.get("last_trade_time")),
        exchange_ts=_parse_dt(item.get("last_trade_time")) or _parse_dt(item.get("timestamp")),
        depth_buy=[
            DepthLevel(d["price"], float(d.get("quantity", 0)), d.get("orders"))
            for d in depth.get("buy") or []
            if d.get("price")
        ],
        depth_sell=[
            DepthLevel(d["price"], float(d.get("quantity", 0)), d.get("orders"))
            for d in depth.get("sell") or []
            if d.get("price")
        ],
        received_at=received_at,
        source="upstox",
    ).finalize()


def parse_instrument(row: dict[str, Any], include_fo: bool) -> InstrumentRecord | None:
    seg = SEGMENTS.get(row.get("segment", ""))
    if not seg:
        return None
    exchange, segment = seg
    itype = (row.get("instrument_type") or "").upper()
    if segment == "EQ" and itype not in ("EQ", "BE"):
        return None
    if segment == "FO" and not include_fo:
        return None
    expiry = None
    if row.get("expiry"):
        exp = row["expiry"]
        try:
            expiry = (
                datetime.fromtimestamp(exp / 1000, IST).date()
                if isinstance(exp, (int, float))
                else date.fromisoformat(str(exp)[:10])
            )
        except (ValueError, OSError):
            expiry = None
    symbol = row.get("trading_symbol") or row.get("tradingsymbol") or ""
    if segment == "INDEX":
        symbol = row.get("name") or symbol
    if not symbol or not row.get("instrument_key"):
        return None
    opt = itype if itype in ("CE", "PE") else None
    return InstrumentRecord(
        provider_key=row["instrument_key"],
        provider_token=str(row.get("exchange_token") or "") or None,
        symbol=symbol,
        exchange=exchange,
        segment=segment,
        name=row.get("name") or row.get("short_name") or symbol,
        isin=row.get("isin") or None,
        instrument_type="INDEX" if segment == "INDEX" else (itype or "EQ"),
        lot_size=int(row.get("lot_size") or 1),
        tick_size=float(row.get("tick_size") or 0.05) / (100 if float(row.get("tick_size") or 0) >= 1 else 1),
        expiry=expiry,
        strike=float(row["strike_price"]) if row.get("strike_price") else None,
        option_type=opt,
        underlying=row.get("underlying_symbol") or None,
        is_index=segment == "INDEX",
    )


class UpstoxProvider(MarketDataProvider):
    provider_type = "upstox"
    display_name = "Upstox"
    supports_streaming = True
    default_rest_url = "https://api.upstox.com"
    default_ws_url = "wss://api.upstox.com/v3/feed/market-data-feed"
    docs_url = "https://upstox.com/developer/api-documentation/"
    default_rate_limit = 20.0
    credential_fields = [
        CredentialField("api_key", "API key (client_id)", secret=False),
        CredentialField("api_secret", "API secret"),
        CredentialField(
            "redirect_uri",
            "Redirect URI",
            secret=False,
            help="Must exactly match the redirect URL registered in the Upstox developer console",
        ),
        CredentialField(
            "access_token",
            "Access token",
            required=False,
            help="Filled automatically by 'Connect Upstox'. You can also paste a token generated elsewhere.",
        ),
    ]
    notes = (
        "Access tokens expire daily at about 03:30 IST. Use 'Connect Upstox' once per trading day. "
        "The V3 WebSocket streams protobuf; full mode includes 5-level depth."
    )

    def _headers(self) -> dict[str, str]:
        token = self.credentials.get("access_token")
        if not token:
            raise AuthenticationRequired("No Upstox access token - use 'Connect Upstox' in Data Provider Settings")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # --- OAuth ------------------------------------------------------------------------
    def authorization_url(self, state: str) -> str:
        if not self.credentials.get("api_key") or not self.credentials.get("redirect_uri"):
            raise AuthenticationRequired("API key and redirect URI are required to start the Upstox login")
        params = {
            "response_type": "code",
            "client_id": self.credentials["api_key"],
            "redirect_uri": self.credentials["redirect_uri"],
            "state": state,
        }
        return f"{self.rest_url}/v2/login/authorization/dialog?{urlencode(params)}"

    async def exchange_code(self, code: str) -> None:
        for field in ("api_key", "api_secret", "redirect_uri"):
            if not self.credentials.get(field):
                raise AuthenticationRequired(f"Missing {field}")
        resp = await request_with_retry(
            self.http,
            "POST",
            f"{self.rest_url}/v2/login/authorization/token",
            limiter=self.rate_limiter,
            retries=1,
            data={
                "code": code,
                "client_id": self.credentials["api_key"],
                "client_secret": self.credentials["api_secret"],
                "redirect_uri": self.credentials["redirect_uri"],
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise ProviderError("Upstox did not return an access token")
        now = datetime.now(UTC)
        self.token_expires_at = next_upstox_token_expiry(now)
        self._save_credentials(
            access_token=token,
            token_obtained_at=now.isoformat(),
            token_expires_at=self.token_expires_at.isoformat(),
            user_id=body.get("user_id"),
        )

    async def authenticate(self) -> None:
        if not self.credentials.get("access_token"):
            raise AuthenticationRequired("No Upstox access token - use 'Connect Upstox' in Data Provider Settings")
        exp = self.credentials.get("token_expires_at")
        if exp:
            self.token_expires_at = datetime.fromisoformat(exp)
            if self.token_expires_at <= datetime.now(UTC):
                raise AuthenticationRequired("Upstox access token expired (daily at ~03:30 IST) - reconnect Upstox")

    # --- REST ---------------------------------------------------------------------------
    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        resp = await request_with_retry(
            self.http,
            "GET",
            f"{self.rest_url}{path}",
            limiter=self.rate_limiter,
            headers=self._headers(),
            params=params or None,
        )
        body = resp.json()
        if body.get("status") not in (None, "success"):
            raise ProviderError(f"Upstox error: {json.dumps(body.get('errors') or body)[:300]}")
        return body

    async def get_quotes(self, provider_keys: list[str]) -> list[Quote]:
        out: list[Quote] = []
        for i in range(0, len(provider_keys), QUOTE_BATCH):
            batch = provider_keys[i : i + QUOTE_BATCH]
            body = await self._get("/v3/market-quote/quotes", instrument_key=",".join(batch))
            now = datetime.now(UTC)
            for item in (body.get("data") or {}).values():
                q = parse_rest_quote(item, now)
                if q:
                    out.append(q)
        return out

    async def get_candles(self, provider_key: str, interval: str, start: datetime, end: datetime) -> list[CandleBar]:
        if interval not in INTERVAL_MAP:
            raise NotSupported(f"Interval {interval} not supported by Upstox")
        unit, n, max_days = INTERVAL_MAP[interval]
        key = quote(provider_key, safe="")
        bars: dict[datetime, CandleBar] = {}
        start_d, end_d = start.astimezone(IST).date(), end.astimezone(IST).date()
        today = datetime.now(IST).date()
        chunk_end = min(end_d, today)
        while chunk_end >= start_d:
            chunk_start = max(start_d, chunk_end - timedelta(days=max_days - 1))
            body = await self._get(
                f"/v3/historical-candle/{key}/{unit}/{n}/{chunk_end.isoformat()}/{chunk_start.isoformat()}"
            )
            for row in (body.get("data") or {}).get("candles") or []:
                bar = self._row_to_bar(row)
                if bar:
                    bars[bar.ts] = bar
            chunk_end = chunk_start - timedelta(days=1)
        if end_d >= today and unit in ("minutes", "hours"):
            body = await self._get(f"/v3/historical-candle/intraday/{key}/{unit}/{n}")
            for row in (body.get("data") or {}).get("candles") or []:
                bar = self._row_to_bar(row)
                if bar:
                    bars[bar.ts] = bar
        return [b for ts, b in sorted(bars.items()) if start <= ts <= end]

    @staticmethod
    def _row_to_bar(row: list[Any]) -> CandleBar | None:
        try:
            ts = _parse_dt(row[0])
            if ts is None:
                return None
            return CandleBar(
                ts=ts.astimezone(UTC),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5] or 0),
                oi=float(row[6]) if len(row) > 6 and row[6] else None,
            )
        except (IndexError, TypeError, ValueError):
            return None

    async def get_instruments(self) -> list[InstrumentRecord]:
        url = self.settings.options.get("instruments_url", INSTRUMENTS_URL)
        resp = await request_with_retry(self.http, "GET", url, retries=2, timeout=120.0)
        raw = resp.content
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass  # already decompressed by the transport
        rows = json.loads(raw)
        include_fo = bool(self.settings.options.get("include_fo", False))
        return [r for r in (parse_instrument(row, include_fo) for row in rows) if r]

    async def get_market_status(self, exchange: str) -> MarketStatusInfo | None:
        body = await self._get(f"/v2/market/status/{exchange}")
        data = body.get("data") or {}
        status = data.get("status", "")
        return MarketStatusInfo(
            exchange=exchange,
            status=UPSTOX_STATUS_MAP.get(status, "closed"),
            source="upstox",
            as_of=_ms(data.get("last_updated")) or datetime.now(UTC),
            detail=status,
        )

    async def get_holidays(self) -> list[HolidayRecord]:
        body = await self._get("/v2/market/holidays")
        out: list[HolidayRecord] = []
        for h in body.get("data") or []:
            if (h.get("holiday_type") or "").upper() not in ("TRADING_HOLIDAY", ""):
                continue
            try:
                day = date.fromisoformat(str(h.get("date"))[:10])
            except ValueError:
                continue
            for ex in h.get("closed_exchanges") or []:
                if ex in ("NSE", "BSE"):
                    out.append(HolidayRecord(ex, day, h.get("description") or ""))
        return out

    # --- Streaming ----------------------------------------------------------------------
    async def _authorized_ws_url(self) -> str:
        try:
            body = await self._get("/v3/feed/market-data-feed/authorize")
            url = (body.get("data") or {}).get("authorized_redirect_uri")
            if url:
                return url
        except AuthenticationRequired:
            raise
        except ProviderError as exc:
            logger.info("Upstox authorize endpoint failed (%s); connecting with bearer header", exc)
        return self.ws_url

    async def stream(self, provider_keys: list[str]) -> AsyncIterator[list[Quote] | MarketStatusInfo]:  # type: ignore[override]
        url = await self._authorized_ws_url()
        mode = (
            self.settings.feed_type
            if self.settings.feed_type in ("ltpc", "full", "option_greeks", "full_d30")
            else "full"
        )
        headers = None if "code=" in url else self._headers()
        async with websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
            open_timeout=15,
        ) as ws:
            for i in range(0, len(provider_keys), 1000):
                req = {
                    "guid": uuid.uuid4().hex,
                    "method": "sub",
                    "data": {"mode": mode, "instrumentKeys": provider_keys[i : i + 1000]},
                }
                await ws.send(json.dumps(req).encode())  # Upstox requires binary frames
            async for frame in ws:
                if isinstance(frame, str):
                    continue
                decoded = upstox_proto.decode(frame)
                if decoded.get("type") == 2:
                    status = market_info_to_status(decoded, self.settings.exchange)
                    if status:
                        yield status
                    continue
                quotes = feed_to_quotes(decoded, datetime.now(UTC))
                if quotes:
                    yield quotes
