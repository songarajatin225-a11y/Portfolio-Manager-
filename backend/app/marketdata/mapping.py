"""JSON field mapping used by the configurable custom REST and WebSocket providers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.marketdata.calendar import IST
from app.marketdata.types import Quote

QUOTE_FIELDS = (
    "ltp",
    "open",
    "high",
    "low",
    "prev_close",
    "change",
    "change_pct",
    "volume",
    "vwap",
    "bid",
    "ask",
    "bid_qty",
    "ask_qty",
    "oi",
    "upper_circuit",
    "lower_circuit",
    "high_52w",
    "low_52w",
)

DEFAULT_QUOTE_MAP = {
    "key": "symbol",
    "ltp": "last_price",
    "open": "open",
    "high": "high",
    "low": "low",
    "prev_close": "prev_close",
    "volume": "volume",
    "vwap": "vwap",
    "bid": "bid",
    "ask": "ask",
    "oi": "oi",
    "timestamp": "timestamp",
}


def get_path(obj: Any, path: str | None) -> Any:
    """Resolve a dotted path like ``data.quote.ltp`` or ``candles.0``. Empty path returns obj."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return datetime.fromtimestamp(v / 1000 if v > 10**11 else v, UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=IST)
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def map_quote(
    item: dict[str, Any],
    mapping: dict[str, str],
    *,
    source: str,
    received_at: datetime,
    key_override: str | None = None,
    delayed: bool = False,
) -> Quote | None:
    key = key_override or get_path(item, mapping.get("key", "symbol"))
    ltp = _num(get_path(item, mapping.get("ltp", "last_price")))
    if not key or not ltp:
        return None
    values = {f: _num(get_path(item, mapping[f])) for f in QUOTE_FIELDS if f != "ltp" and mapping.get(f)}
    ts = parse_timestamp(get_path(item, mapping.get("timestamp", "timestamp")))
    return Quote(
        provider_key=str(key),
        ltp=ltp,
        exchange_ts=ts,
        received_at=received_at,
        source=source,
        provider_delayed=delayed,
        **values,
    ).finalize()


def extract_items(body: Any, items_path: str | None) -> list[tuple[str | None, dict[str, Any]]]:
    """Return (dict_key, item) pairs from a list or a dict-of-items located at ``items_path``."""
    items = get_path(body, items_path)
    if isinstance(items, list):
        return [(None, i) for i in items if isinstance(i, dict)]
    if isinstance(items, dict):
        if all(isinstance(v, dict) for v in items.values()) and items:
            return [(k, v) for k, v in items.items()]
        return [(None, items)]
    return []
