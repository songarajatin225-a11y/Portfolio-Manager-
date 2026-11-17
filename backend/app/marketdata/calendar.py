"""NSE/BSE trading calendar.

Regular equity session (IST): pre-open 09:00-09:15, continuous 09:15-15:30, post-close
15:40-16:00. Weekends are closed. Exchange holidays come from the provider when it publishes
them (Upstox) or from the admin-maintained ``market_holidays`` table. Special sessions such as
Muhurat trading are announced annually and are not modelled; the provider-reported market status
takes precedence over this calendar whenever it is available.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

PRE_OPEN_START = time(9, 0)
OPEN = time(9, 15)
CLOSE = time(15, 30)
POST_CLOSE_END = time(16, 0)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def session_state(now: datetime, holidays: Iterable[date] = ()) -> str:
    """Return one of: weekend, holiday, pre_open, open, post_close, closed."""
    local = to_ist(now)
    if local.weekday() >= 5:
        return "weekend"
    if local.date() in set(holidays):
        return "holiday"
    t = local.time()
    if PRE_OPEN_START <= t < OPEN:
        return "pre_open"
    if OPEN <= t < CLOSE:
        return "open"
    if CLOSE <= t < POST_CLOSE_END:
        return "post_close"
    return "closed"


def is_trading_day(d: date, holidays: Iterable[date] = ()) -> bool:
    return d.weekday() < 5 and d not in set(holidays)


def previous_trading_day(d: date, holidays: Iterable[date] = ()) -> date:
    hs = set(holidays)
    d -= timedelta(days=1)
    while not is_trading_day(d, hs):
        d -= timedelta(days=1)
    return d


def session_bounds(d: date) -> tuple[datetime, datetime]:
    """UTC start/end of the continuous session on trading day ``d``."""
    start = datetime.combine(d, OPEN, IST).astimezone(UTC)
    end = datetime.combine(d, CLOSE, IST).astimezone(UTC)
    return start, end


def next_upstox_token_expiry(now: datetime) -> datetime:
    """Upstox access tokens expire at 03:30 IST on the following day."""
    local = to_ist(now)
    expiry = datetime.combine(local.date(), time(3, 30), IST)
    if local >= expiry:
        expiry += timedelta(days=1)
    return expiry.astimezone(UTC)
