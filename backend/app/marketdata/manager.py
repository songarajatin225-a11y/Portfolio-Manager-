"""Feed manager - owns the single upstream market data connection (runs in the worker).

Responsibilities
* Load the active provider config; rebuild the connection when an admin changes it.
* Authenticate and refresh tokens (persisting new tokens encrypted).
* Resolve the tracked universe to provider keys and subscribe (stream) or poll (REST).
* Reconnect with exponential backoff + jitter; honour provider rate limits.
* Validate every quote, merge partial stream updates with the last snapshot, label it, cache it,
  and publish it to the API process for WebSocket fan-out.
* Build 1-minute candles from ticks and persist them.
* Track feed health (state, last message, latency, reconnects, errors, subscriptions).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
import time
from collections import deque
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.bus import CH_COMMANDS, CH_FEED_STATUS, CH_TICKS, K_FEED_HEALTH, K_QUOTE, get_bus
from app.db.models import Instrument, MarketHoliday, ProviderConfig
from app.db.session import get_sessionmaker
from app.marketdata.base import AuthenticationRequired, MarketDataProvider, NotSupported, ProviderError
from app.marketdata.calendar import session_state
from app.marketdata.candle_builder import CandleBuilder, CompletedBar
from app.marketdata.http import backoff_delays
from app.marketdata.registry import build_provider
from app.marketdata.types import DataStatus, MarketStatusInfo, Quote, validate_quote
from app.services import app_settings
from app.services import instruments as instrument_service
from app.services.candles import upsert_candles
from app.services.market_data import holidays as load_holidays
from app.services.market_data import persist_credentials

logger = logging.getLogger(__name__)

QUOTE_TTL = 3 * 24 * 3600
MERGE_FIELDS = [
    f.name
    for f in fields(Quote)
    if f.name
    not in (
        "provider_key",
        "ltp",
        "received_at",
        "source",
        "instrument_id",
        "symbol",
        "exchange",
        "exchange_ts",
        "last_trade_time",
        "change",
        "change_pct",
        "is_simulated",
        "provider_delayed",
    )
]


def tick_payload(q: Quote, status: str) -> dict[str, Any]:
    return {
        "instrument_id": q.instrument_id,
        "symbol": q.symbol,
        "exchange": q.exchange,
        "ltp": q.ltp,
        "change": q.change,
        "change_pct": q.change_pct,
        "open": q.open,
        "high": q.high,
        "low": q.low,
        "prev_close": q.prev_close,
        "volume": q.volume,
        "vwap": q.vwap,
        "bid": q.bid,
        "ask": q.ask,
        "oi": q.oi,
        "received_at": q.received_at.isoformat(),
        "exchange_ts": q.exchange_ts.isoformat() if q.exchange_ts else None,
        "source": q.source,
        "data_status": status,
        "is_simulated": q.is_simulated,
    }


class FeedManager:
    def __init__(self) -> None:
        self.reload = asyncio.Event()
        self.provider: MarketDataProvider | None = None
        self.cfg_id: int | None = None
        self.key_to_id: dict[str, int] = {}
        self.meta: dict[int, tuple[str, str]] = {}
        self.last: dict[int, Quote] = {}
        self.builder = CandleBuilder()
        self.pending_bars: list[CompletedBar] = []
        self.latencies: deque[float] = deque(maxlen=500)
        self.msg_times: deque[float] = deque(maxlen=20000)
        self.market_status: dict[str, dict[str, Any]] = {}
        self.holidays: set = set()
        self.health: dict[str, Any] = {"state": "starting", "reconnects": 0, "invalid_quotes": 0}
        self.stale_after = 30.0
        self.poll_seconds = 5.0

    # --- health ---------------------------------------------------------------------------
    def _set(self, **kw: Any) -> None:
        self.health.update(kw)

    async def publish_health(self) -> None:
        now = time.monotonic()
        while self.msg_times and now - self.msg_times[0] > 60:
            self.msg_times.popleft()
        lat = sorted(self.latencies)
        self.health.update(
            messages_last_minute=len(self.msg_times),
            latency_ms_p50=round(statistics.median(lat), 1) if lat else None,
            latency_ms_p95=round(lat[int(len(lat) * 0.95) - 1], 1) if len(lat) >= 20 else None,
            market_status=self.market_status,
            stale_after_seconds=self.stale_after,
            updated_at=datetime.now(UTC).isoformat(),
        )
        bus = get_bus()
        await bus.set_json(K_FEED_HEALTH, self.health, ttl_seconds=120)
        await bus.publish(CH_FEED_STATUS, self.health)

    async def _health_loop(self) -> None:
        while True:
            await self.publish_health()
            await asyncio.sleep(5)

    # --- commands ---------------------------------------------------------------------------
    async def _command_loop(self) -> None:
        async for _, msg in get_bus().subscribe(CH_COMMANDS):
            if msg.get("cmd") in ("reload", "resubscribe"):
                logger.info("Feed manager received %s", msg.get("cmd"))
                self.reload.set()

    async def _wait_reload(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.reload.wait(), timeout=seconds)
        self.reload.clear()

    # --- quote processing ----------------------------------------------------------------
    def _status_for(self, q: Quote) -> str:
        if q.is_simulated:
            return DataStatus.SIMULATED.value
        if q.provider_delayed:
            return DataStatus.DELAYED.value
        exch = q.exchange or "NSE"
        state = (self.market_status.get(exch) or {}).get("status") or session_state(datetime.now(UTC), self.holidays)
        return DataStatus.LIVE.value if state == "open" else DataStatus.HISTORICAL.value

    async def process(self, quotes: list[Quote]) -> int:
        assert self.provider is not None
        accepted: list[Quote] = []
        now = datetime.now(UTC)
        for q in quotes:
            iid = self.key_to_id.get(q.provider_key)
            if iid is None:
                continue
            q.instrument_id = iid
            q.symbol, q.exchange = self.meta.get(iid, (None, None))
            q.is_simulated = self.provider.is_simulated
            prev = self.last.get(iid)
            if prev is not None:  # streams often send partial updates (e.g. LTP only)
                for name in MERGE_FIELDS:
                    if getattr(q, name) in (None, []) and getattr(prev, name) not in (None, []):
                        setattr(q, name, getattr(prev, name))
                q.change = q.change_pct = None  # recompute against the merged prev_close
            q.finalize()
            problems = validate_quote(q, now)
            if problems:
                self.health["invalid_quotes"] = self.health.get("invalid_quotes", 0) + 1
                self.health["last_invalid"] = {"symbol": q.symbol, "problems": problems, "at": now.isoformat()}
                continue
            if q.exchange_ts:
                self.latencies.append(max(0.0, (q.received_at - q.exchange_ts).total_seconds() * 1000))
            self.last[iid] = q
            accepted.append(q)
            self.pending_bars.extend(self.builder.on_quote(q))
        if not accepted:
            return 0
        bus = get_bus()
        await bus.set_many_json({f"{K_QUOTE}{q.instrument_id}": q.to_cache() for q in accepted}, ttl_seconds=QUOTE_TTL)
        await bus.publish(CH_TICKS, {"quotes": [tick_payload(q, self._status_for(q)) for q in accepted]})
        mono = time.monotonic()
        self.msg_times.extend([mono] * len(accepted))
        self._set(last_message_at=now.isoformat(), last_batch_size=len(accepted))
        return len(accepted)

    async def _flush_bars_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            self.pending_bars.extend(self.builder.flush_idle(datetime.now(UTC)))
            if not self.pending_bars:
                continue
            bars, self.pending_bars = self.pending_bars, []
            try:
                async with get_sessionmaker()() as db:
                    grouped: dict[tuple[int, bool, str], list] = {}
                    for cb in bars:
                        grouped.setdefault((cb.instrument_id, cb.simulated, cb.source), []).append(cb.bar)
                    for (iid, sim, src), items in grouped.items():
                        await upsert_candles(db, iid, "1m", items, src, sim)
                    await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to persist %d 1-minute bars", len(bars))

    # --- setup ---------------------------------------------------------------------------
    async def _load(self) -> ProviderConfig | None:
        async with get_sessionmaker()() as db:
            cfg = await db.scalar(select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1))
            refresh = await app_settings.get_section(db, "refresh")
            self.stale_after = float(refresh["stale_after_seconds"])
            self.poll_seconds = float(refresh["quote_poll_seconds"])
            self.holidays = await load_holidays(db)
            return cfg

    async def _resolve_universe(self, cfg: ProviderConfig) -> list[str]:
        async with get_sessionmaker()() as db:
            ids = await instrument_service.tracked_universe(db)
            keys = await instrument_service.provider_keys(db, cfg.provider_type, ids)
            rows = (
                await db.execute(
                    select(Instrument.id, Instrument.symbol, Instrument.exchange).where(Instrument.id.in_(list(keys)))
                )
            ).all()
        self.meta = {r.id: (r.symbol, r.exchange) for r in rows}
        self.key_to_id = {v: k for k, v in keys.items()}
        return list(keys.values())

    async def _authenticate(self, cfg_id: int) -> None:
        assert self.provider is not None
        await self.provider.authenticate()
        async with get_sessionmaker()() as db:
            cfg = await db.get(ProviderConfig, cfg_id)
            if cfg is not None:
                await persist_credentials(db, cfg, self.provider)
                await db.commit()
        if self.provider.token_expires_at:
            self._set(token_expires_at=self.provider.token_expires_at.isoformat())

    async def _sync_calendar(self) -> None:
        assert self.provider is not None
        try:
            hols = await self.provider.get_holidays()
        except (ProviderError, NotSupported) as exc:
            logger.info("Holiday sync skipped: %s", exc)
            return
        if not hols:
            return
        async with get_sessionmaker()() as db:
            for h in hols:
                await db.execute(
                    insert(MarketHoliday)
                    .values(
                        exchange=h.exchange,
                        holiday_date=h.day,
                        description=h.description,
                        source=self.provider.provider_type,
                    )
                    .on_conflict_do_nothing()
                )
            await db.commit()
            self.holidays = await load_holidays(db)

    def _set_market_status(self, s: MarketStatusInfo) -> None:
        self.market_status[s.exchange] = {
            "status": s.status,
            "source": s.source,
            "as_of": s.as_of.isoformat(),
            "detail": s.detail,
        }

    async def _market_status_loop(self, exchange: str) -> None:
        assert self.provider is not None
        while True:
            try:
                status = await self.provider.get_market_status(exchange)
                if status:
                    self._set_market_status(status)
                else:
                    state = session_state(datetime.now(UTC), self.holidays)
                    self._set_market_status(MarketStatusInfo(exchange, state, "calendar", datetime.now(UTC)))
            except (ProviderError, NotSupported) as exc:
                logger.info("Market status unavailable: %s", exc)
                state = session_state(datetime.now(UTC), self.holidays)
                self._set_market_status(MarketStatusInfo(exchange, state, "calendar", datetime.now(UTC)))
            await asyncio.sleep(120)

    async def _token_loop(self, cfg_id: int) -> None:
        """Refresh tokens before expiry (Angel One) or flag expiry (Upstox)."""
        assert self.provider is not None
        while True:
            await asyncio.sleep(300)
            exp = self.provider.token_expires_at
            if exp and exp - datetime.now(UTC) < timedelta(minutes=45):
                try:
                    await self._authenticate(cfg_id)
                    logger.info("Provider session refreshed")
                except AuthenticationRequired as exc:
                    self._set(state="auth_required", last_error=str(exc), last_error_at=datetime.now(UTC).isoformat())
                    self.reload.set()
                    return

    # --- data loops ------------------------------------------------------------------------
    async def _snapshot(self, keys: list[str]) -> None:
        assert self.provider is not None
        if not keys:
            return
        try:
            await self.process(await self.provider.get_quotes(keys))
        except NotSupported:
            pass

    async def _stream_loop(self, keys: list[str]) -> None:
        assert self.provider is not None
        delays = backoff_delays(1.0, 60.0)
        while True:
            try:
                self._set(state="connecting")
                connected_at = time.monotonic()
                async for item in self.provider.stream(keys):
                    if self.health.get("state") != "connected":
                        self._set(state="connected", connected_since=datetime.now(UTC).isoformat(), last_error=None)
                    if isinstance(item, MarketStatusInfo):
                        self._set_market_status(item)
                    else:
                        await self.process(item)
                    if time.monotonic() - connected_at > 120:
                        delays = backoff_delays(1.0, 60.0)  # healthy connection: reset backoff
                raise ProviderError("Stream ended")
            except asyncio.CancelledError:
                raise
            except AuthenticationRequired:
                raise
            except Exception as exc:  # noqa: BLE001 - any stream failure triggers reconnect
                delay = next(delays)
                self.health["reconnects"] = self.health.get("reconnects", 0) + 1
                self._set(
                    state="reconnecting",
                    last_error=f"{type(exc).__name__}: {str(exc)[:200]}",
                    last_error_at=datetime.now(UTC).isoformat(),
                    next_retry_in=round(delay, 1),
                )
                logger.warning("Market stream disconnected (%s); reconnecting in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                with contextlib.suppress(Exception):
                    await self.provider.authenticate()

    async def _poll_loop(self, keys: list[str]) -> None:
        assert self.provider is not None
        self._set(state="polling")
        delays = backoff_delays(2.0, 60.0)
        while True:
            try:
                await self.process(await self.provider.get_quotes(keys))
                self._set(state="polling", last_error=None)
                delays = backoff_delays(2.0, 60.0)
                state = session_state(datetime.now(UTC), self.holidays)
                await asyncio.sleep(self.poll_seconds if state in ("open", "pre_open") else max(60, self.poll_seconds))
            except asyncio.CancelledError:
                raise
            except AuthenticationRequired:
                raise
            except Exception as exc:  # noqa: BLE001
                delay = next(delays)
                self._set(
                    state="degraded",
                    last_error=f"{type(exc).__name__}: {str(exc)[:200]}",
                    last_error_at=datetime.now(UTC).isoformat(),
                )
                await asyncio.sleep(delay)

    # --- main --------------------------------------------------------------------------------
    async def run(self) -> None:
        helpers = [
            asyncio.create_task(self._command_loop()),
            asyncio.create_task(self._health_loop()),
            asyncio.create_task(self._flush_bars_loop()),
        ]
        try:
            while True:
                await self._run_once()
        finally:
            for t in helpers:
                t.cancel()
            await self._teardown()

    async def _teardown(self) -> None:
        if self.provider is not None:
            with contextlib.suppress(Exception):
                await self.provider.close()
        self.provider = None

    async def _run_once(self) -> None:
        await self._teardown()
        cfg = await self._load()
        if cfg is None:
            self._set(state="not_configured", provider_name=None, provider_type=None, subscribed=0, last_error=None)
            await self._wait_reload(30)
            return
        try:
            self.provider = build_provider(cfg)
        except Exception as exc:  # noqa: BLE001
            self._set(state="error", provider_name=cfg.name, provider_type=cfg.provider_type, last_error=str(exc))
            await self._wait_reload(60)
            return
        self.cfg_id = cfg.id
        self.last.clear()
        self._set(
            state="authenticating",
            provider_name=cfg.name,
            provider_type=cfg.provider_type,
            is_simulated=self.provider.is_simulated,
            streaming=self.provider.supports_streaming,
            last_error=None,
            reconnects=0,
            connected_since=None,
        )
        try:
            await self._authenticate(cfg.id)
        except AuthenticationRequired as exc:
            self._set(state="auth_required", last_error=str(exc), last_error_at=datetime.now(UTC).isoformat())
            await self._wait_reload(120)
            return
        except ProviderError as exc:
            self._set(state="error", last_error=str(exc), last_error_at=datetime.now(UTC).isoformat())
            await self._wait_reload(60)
            return

        keys = await self._resolve_universe(cfg)
        self._set(subscribed=len(keys))
        if not keys:
            self._set(
                state="idle",
                last_error="No instruments to track. Sync the instrument master and add "
                "instruments to a watchlist or the tracked universe index.",
            )
            await self._wait_reload(60)
            return

        await self._sync_calendar()
        tasks = [
            asyncio.create_task(self._market_status_loop(cfg.exchange)),
            asyncio.create_task(self._token_loop(cfg.id)),
        ]
        try:
            await self._snapshot(keys)
            runner = self._stream_loop(keys) if self.provider.supports_streaming else self._poll_loop(keys)
            main = asyncio.create_task(runner)
            reload_wait = asyncio.create_task(self.reload.wait())
            refresh = asyncio.create_task(asyncio.sleep(1800))  # periodic universe refresh
            done, _ = await asyncio.wait({main, reload_wait, refresh}, return_when=asyncio.FIRST_COMPLETED)
            for t in (main, reload_wait, refresh):
                if t not in done:
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await t  # let the stream close its socket before reconnecting
            self.reload.clear()
            if main in done and main.exception():
                raise main.exception()  # type: ignore[misc]
        except AuthenticationRequired as exc:
            self._set(state="auth_required", last_error=str(exc), last_error_at=datetime.now(UTC).isoformat())
            await self._wait_reload(120)
        except ProviderError as exc:
            self._set(state="error", last_error=str(exc), last_error_at=datetime.now(UTC).isoformat())
            await self._wait_reload(30)
        finally:
            for t in tasks:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t


async def run_feed_manager() -> None:
    await FeedManager().run()
