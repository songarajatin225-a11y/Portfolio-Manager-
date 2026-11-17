"""Angel One SmartAPI provider.

Routes, headers and the WebSocket 2.0 binary layout were checked against the official
``smartapi-python`` 1.5.5 package. Login needs client code + PIN + a TOTP generated from the
account's TOTP secret, so the backend can re-authenticate daily without human action.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import struct
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
import websockets

from app.marketdata.base import (
    AuthenticationRequired,
    CredentialField,
    MarketDataProvider,
    NotSupported,
    ProviderError,
)
from app.marketdata.calendar import IST
from app.marketdata.http import request_with_retry
from app.marketdata.types import CandleBar, DepthLevel, InstrumentRecord, Quote

logger = logging.getLogger(__name__)

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
QUOTE_BATCH = 50  # SmartAPI quote endpoint accepts up to 50 tokens per request

INTERVAL_MAP: dict[str, tuple[str, int]] = {
    "1m": ("ONE_MINUTE", 30),
    "5m": ("FIVE_MINUTE", 90),
    "15m": ("FIFTEEN_MINUTE", 180),
    "30m": ("THIRTY_MINUTE", 180),
    "1h": ("ONE_HOUR", 365),
    "1d": ("ONE_DAY", 2000),
}

EXCHANGE_TYPES = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5}
EXCHANGE_TYPE_NAMES = {v: k for k, v in EXCHANGE_TYPES.items()}
MODE_LTP, MODE_QUOTE, MODE_SNAP = 1, 2, 3
HEARTBEAT_SECONDS = 10


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _mac() -> str:
    node = uuid.getnode()
    return ":".join(f"{(node >> ele) & 0xFF:02x}" for ele in range(40, -1, -8))


def _parse_angel_time(value: Any) -> datetime | None:
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(str(value), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def split_key(provider_key: str) -> tuple[str, str]:
    """Provider keys are stored as ``EXCHANGE:TOKEN`` (e.g. ``NSE:2885``)."""
    exchange, _, token = provider_key.partition(":")
    if not token:
        raise ProviderError(f"Invalid Angel One instrument key '{provider_key}'")
    return exchange, token


# --- WebSocket 2.0 binary packet ------------------------------------------------------


def parse_binary_tick(data: bytes, received_at: datetime) -> Quote | None:
    """Parse one SmartAPI WebSocket 2.0 packet (little-endian). Prices arrive in paise."""
    if len(data) < 51:
        return None
    mode = data[0]
    exch_type = data[1]
    token = data[2:27].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
    (exch_ts,) = struct.unpack_from("<q", data, 35)
    (ltp_paise,) = struct.unpack_from("<q", data, 43)
    divisor = 100.0
    exchange = EXCHANGE_TYPE_NAMES.get(exch_type, str(exch_type))
    exch_dt = datetime.fromtimestamp(exch_ts / 1000, UTC) if exch_ts else None
    q = Quote(
        provider_key=f"{exchange}:{token}",
        ltp=ltp_paise / divisor,
        exchange_ts=exch_dt,
        received_at=received_at,
        source="angelone",
    )
    if mode in (MODE_QUOTE, MODE_SNAP) and len(data) >= 123:
        ltq, atp, vol = struct.unpack_from("<qqq", data, 51)
        tbq, tsq = struct.unpack_from("<dd", data, 75)
        o, h, low, c = struct.unpack_from("<qqqq", data, 91)
        q.vwap = atp / divisor or None
        q.volume = float(vol)
        q.total_buy_qty, q.total_sell_qty = tbq, tsq
        q.open, q.high, q.low = o / divisor or None, h / divisor or None, low / divisor or None
        q.prev_close = c / divisor or None  # "close" in the packet is the previous session close
    if mode == MODE_SNAP and len(data) >= 379:
        (ltt,) = struct.unpack_from("<q", data, 123)
        (oi,) = struct.unpack_from("<q", data, 131)
        if ltt:  # unit is undocumented in the SDK; accept seconds or milliseconds
            q.last_trade_time = datetime.fromtimestamp(ltt / 1000 if ltt > 10**11 else ltt, UTC)
        q.oi = float(oi) or None
        for i in range(10):
            off = 147 + i * 20
            flag, qty, price, orders = struct.unpack_from("<HqqH", data, off)
            if price <= 0:
                continue
            level = DepthLevel(price / divisor, float(qty), orders)
            (q.depth_buy if flag == 0 else q.depth_sell).append(level)
        uc, lc, h52, l52 = struct.unpack_from("<qqqq", data, 347)
        q.upper_circuit, q.lower_circuit = uc / divisor or None, lc / divisor or None
        q.high_52w, q.low_52w = h52 / divisor or None, l52 / divisor or None
    return q.finalize()


def parse_scrip(row: dict[str, Any], include_fo: bool) -> InstrumentRecord | None:
    exch = row.get("exch_seg")
    token = str(row.get("token") or "")
    symbol = row.get("symbol") or ""
    itype = (row.get("instrumenttype") or "").upper()
    if not token or exch not in ("NSE", "BSE", "NFO", "BFO"):
        return None
    if exch in ("NFO", "BFO"):
        if not include_fo:
            return None
        segment, exchange = "FO", "NSE" if exch == "NFO" else "BSE"
    elif itype == "AMXIDX":
        segment, exchange = "INDEX", exch
    elif itype == "" and (symbol.endswith("-EQ") or exch == "BSE"):
        segment, exchange = "EQ", exch
    else:
        return None
    expiry = None
    if row.get("expiry"):
        with contextlib.suppress(ValueError):
            expiry = datetime.strptime(row["expiry"], "%d%b%Y").date()
    strike = None
    try:
        s = float(row.get("strike") or -1)
        strike = s / 100 if s > 0 else None
    except ValueError:
        pass
    if segment == "EQ":
        canonical = row.get("name") or symbol.removesuffix("-EQ")
    elif segment == "INDEX":
        canonical = symbol  # e.g. "Nifty 50"
    else:
        canonical = symbol
    opt = symbol[-2:] if segment == "FO" and symbol[-2:] in ("CE", "PE") else None
    return InstrumentRecord(
        provider_key=f"{exch}:{token}",
        provider_token=token,
        symbol=canonical,
        exchange=exchange,
        segment=segment,
        name=row.get("name") or canonical,
        instrument_type="INDEX" if segment == "INDEX" else (itype or "EQ"),
        lot_size=int(float(row.get("lotsize") or 1)),
        tick_size=(float(row.get("tick_size") or 5) / 100),
        expiry=expiry,
        strike=strike,
        option_type=opt,
        underlying=row.get("name") if segment == "FO" else None,
        is_index=segment == "INDEX",
    )


class AngelOneProvider(MarketDataProvider):
    provider_type = "angelone"
    display_name = "Angel One SmartAPI"
    supports_streaming = True
    default_rest_url = "https://apiconnect.angelone.in"
    default_ws_url = "wss://smartapisocket.angelone.in/smart-stream"
    docs_url = "https://smartapi.angelbroking.com/docs"
    default_rate_limit = 3.0
    credential_fields = [
        CredentialField("api_key", "API key", secret=True),
        CredentialField("client_code", "Client code", secret=False),
        CredentialField("pin", "Login PIN"),
        CredentialField("totp_secret", "TOTP secret", help="The base32 secret shown when enabling TOTP for SmartAPI"),
    ]
    notes = "Logs in automatically with PIN + TOTP and refreshes the session daily. Prices on the feed are in paise."

    def _headers(self, auth: bool = True) -> dict[str, str]:
        api_key = self.credentials.get("api_key")
        if not api_key:
            raise AuthenticationRequired("Angel One API key is not configured")
        opts = self.settings.options
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": opts.get("client_local_ip") or _local_ip(),
            "X-ClientPublicIP": opts.get("client_public_ip") or _local_ip(),
            "X-MACAddress": opts.get("mac_address") or _mac(),
            "X-PrivateKey": api_key,
        }
        if auth:
            jwt = self.credentials.get("jwt_token")
            if not jwt:
                raise AuthenticationRequired("Not logged in to Angel One")
            h["Authorization"] = f"Bearer {jwt}"
        return h

    async def _post(self, path: str, payload: dict[str, Any], auth: bool = True) -> dict[str, Any]:
        resp = await request_with_retry(
            self.http,
            "POST",
            f"{self.rest_url}{path}",
            limiter=self.rate_limiter,
            headers=self._headers(auth),
            json=payload,
        )
        body = resp.json()
        if body.get("status") is False or body.get("success") is False:
            code = body.get("errorcode") or body.get("errorCode") or ""
            message = body.get("message") or "Angel One request failed"
            if code in ("AG8001", "AG8002", "AG8003", "AB1010", "AB8050", "AB8051"):
                raise AuthenticationRequired(f"{message} ({code})")
            raise ProviderError(f"{message} ({code})")
        return body

    async def _login(self) -> None:
        for field in ("api_key", "client_code", "pin", "totp_secret"):
            if not self.credentials.get(field):
                raise AuthenticationRequired(f"Angel One {field.replace('_', ' ')} is not configured")
        try:
            totp = pyotp.TOTP(self.credentials["totp_secret"].replace(" ", "")).now()
        except (ValueError, TypeError) as exc:
            raise AuthenticationRequired("Angel One TOTP secret is not a valid base32 string") from exc
        body = await self._post(
            "/rest/auth/angelbroking/user/v1/loginByPassword",
            {"clientcode": self.credentials["client_code"], "password": self.credentials["pin"], "totp": totp},
            auth=False,
        )
        data = body.get("data") or {}
        if not data.get("jwtToken"):
            raise AuthenticationRequired("Angel One login did not return a session token")
        self._store_session(data)

    def _store_session(self, data: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        # Sessions are valid until midnight IST; refresh well before.
        local_midnight = datetime.combine(now.astimezone(IST).date() + timedelta(days=1), datetime.min.time(), IST)
        self.token_expires_at = local_midnight.astimezone(UTC)
        self._save_credentials(
            jwt_token=data["jwtToken"],
            refresh_token=data.get("refreshToken"),
            feed_token=data.get("feedToken"),
            token_obtained_at=now.isoformat(),
            token_expires_at=self.token_expires_at.isoformat(),
        )

    async def authenticate(self) -> None:
        exp = self.credentials.get("token_expires_at")
        if self.credentials.get("jwt_token") and exp:
            expires = datetime.fromisoformat(exp)
            if expires - datetime.now(UTC) > timedelta(minutes=30):
                self.token_expires_at = expires
                return
            if self.credentials.get("refresh_token") and expires > datetime.now(UTC):
                try:
                    body = await self._post(
                        "/rest/auth/angelbroking/jwt/v1/generateTokens",
                        {"refreshToken": self.credentials["refresh_token"]},
                    )
                    self._store_session(body.get("data") or {})
                    return
                except (AuthenticationRequired, ProviderError, KeyError):
                    logger.info("Angel One token refresh failed; performing a fresh login")
        await self._login()

    async def get_quotes(self, provider_keys: list[str]) -> list[Quote]:
        grouped: dict[str, list[str]] = {}
        for key in provider_keys:
            ex, token = split_key(key)
            grouped.setdefault(ex, []).append(token)
        out: list[Quote] = []
        flat = [(ex, t) for ex, tokens in grouped.items() for t in tokens]
        for i in range(0, len(flat), QUOTE_BATCH):
            chunk: dict[str, list[str]] = {}
            for ex, t in flat[i : i + QUOTE_BATCH]:
                chunk.setdefault(ex, []).append(t)
            body = await self._post(
                "/rest/secure/angelbroking/market/v1/quote/", {"mode": "FULL", "exchangeTokens": chunk}
            )
            now = datetime.now(UTC)
            for item in (body.get("data") or {}).get("fetched") or []:
                q = self._parse_quote(item, now)
                if q:
                    out.append(q)
        return out

    @staticmethod
    def _parse_quote(item: dict[str, Any], received_at: datetime) -> Quote | None:
        if not item.get("ltp"):
            return None
        depth = item.get("depth") or {}
        exch_time = _parse_angel_time(item.get("exchFeedTime"))
        return Quote(
            provider_key=f"{item.get('exchange')}:{item.get('symbolToken')}",
            ltp=float(item["ltp"]),
            open=item.get("open") or None,
            high=item.get("high") or None,
            low=item.get("low") or None,
            prev_close=item.get("close") or None,
            change=item.get("netChange"),
            change_pct=item.get("percentChange"),
            volume=float(item["tradeVolume"]) if item.get("tradeVolume") is not None else None,
            vwap=item.get("avgPrice") or None,
            oi=item.get("opnInterest") or None,
            total_buy_qty=item.get("totBuyQuan"),
            total_sell_qty=item.get("totSellQuan"),
            upper_circuit=item.get("upperCircuit") or None,
            lower_circuit=item.get("lowerCircuit") or None,
            high_52w=item.get("52WeekHigh") or None,
            low_52w=item.get("52WeekLow") or None,
            last_trade_time=_parse_angel_time(item.get("exchTradeTime")),
            exchange_ts=exch_time,
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
            source="angelone",
        ).finalize()

    async def get_candles(self, provider_key: str, interval: str, start: datetime, end: datetime) -> list[CandleBar]:
        if interval in ("1w", "1mo"):
            from app.analytics.resample import resample_bars

            daily = await self.get_candles(provider_key, "1d", start, end)
            return resample_bars(daily, interval)
        if interval not in INTERVAL_MAP:
            raise NotSupported(f"Interval {interval} not supported by Angel One")
        name, max_days = INTERVAL_MAP[interval]
        exchange, token = split_key(provider_key)
        bars: dict[datetime, CandleBar] = {}
        cursor = start.astimezone(IST)
        end_l = end.astimezone(IST)
        while cursor <= end_l:
            chunk_end = min(end_l, cursor + timedelta(days=max_days))
            body = await self._post(
                "/rest/secure/angelbroking/historical/v1/getCandleData",
                {
                    "exchange": exchange,
                    "symboltoken": token,
                    "interval": name,
                    "fromdate": cursor.strftime("%Y-%m-%d %H:%M"),
                    "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
                },
            )
            for row in body.get("data") or []:
                try:
                    ts = datetime.fromisoformat(row[0]).astimezone(UTC)
                    bars[ts] = CandleBar(
                        ts=ts,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5] or 0),
                    )
                except (IndexError, TypeError, ValueError):
                    continue
            cursor = chunk_end + timedelta(minutes=1)
        return [b for _, b in sorted(bars.items())]

    async def get_instruments(self) -> list[InstrumentRecord]:
        url = self.settings.options.get("instruments_url", SCRIP_MASTER_URL)
        resp = await request_with_retry(self.http, "GET", url, retries=2, timeout=120.0)
        include_fo = bool(self.settings.options.get("include_fo", False))
        return [r for r in (parse_scrip(row, include_fo) for row in resp.json()) if r]

    async def stream(self, provider_keys: list[str]) -> AsyncIterator[list[Quote]]:  # type: ignore[override]
        await self.authenticate()
        if not self.credentials.get("feed_token"):
            raise AuthenticationRequired("Angel One feed token missing - log in again")
        headers = {
            "Authorization": self.credentials["jwt_token"],
            "x-api-key": self.credentials["api_key"],
            "x-client-code": self.credentials["client_code"],
            "x-feed-token": self.credentials["feed_token"],
        }
        mode = {"ltp": MODE_LTP, "quote": MODE_QUOTE}.get(self.settings.feed_type, MODE_SNAP)
        grouped: dict[int, list[str]] = {}
        for key in provider_keys:
            ex, token = split_key(key)
            grouped.setdefault(EXCHANGE_TYPES.get(ex, 1), []).append(token)
        async with websockets.connect(
            self.ws_url, additional_headers=headers, ping_interval=None, max_size=4 * 1024 * 1024, open_timeout=15
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "correlationID": uuid.uuid4().hex[:10],
                        "action": 1,
                        "params": {
                            "mode": mode,
                            "tokenList": [{"exchangeType": k, "tokens": v} for k, v in grouped.items()],
                        },
                    }
                )
            )

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(HEARTBEAT_SECONDS)
                    await ws.send("ping")

            hb = asyncio.create_task(heartbeat())
            try:
                async for frame in ws:
                    if isinstance(frame, str):
                        if frame != "pong":
                            logger.info("Angel One feed message: %s", frame[:200])
                        continue
                    q = parse_binary_tick(frame, datetime.now(UTC))
                    if q:
                        yield [q]
            finally:
                hb.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await hb

    async def get_holidays(self) -> list:
        return []  # SmartAPI does not publish a holiday calendar; admin-maintained holidays are used.


__all__ = ["AngelOneProvider", "parse_binary_tick", "parse_scrip", "split_key"]
