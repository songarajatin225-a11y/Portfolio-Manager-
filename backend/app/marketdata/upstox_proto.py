"""Minimal protobuf decoder for Upstox's ``MarketDataFeedV3`` messages.

Field numbers and types were taken from the compiled descriptor shipped in the official
``upstox-python-sdk`` 2.30.0 package (``upstox_client/feeder/proto/MarketDataFeedV3_pb2.py``,
package ``com.upstox.marketdatafeederv3udapi.rpc.proto``). Decoding the wire format directly
avoids generated code and a protoc build step. Unknown fields are skipped, so additive changes
on Upstox's side do not break decoding.
"""

from __future__ import annotations

import struct
from typing import Any

# type codes
DOUBLE, INT64, STRING, BOOL, ENUM, MSG = "double", "int64", "string", "bool", "enum", "msg"

# message -> field number -> (name, type, sub-message, repeated)
SCHEMA: dict[str, dict[int, tuple[str, str, str | None, bool]]] = {
    "DoubleValue": {1: ("value", DOUBLE, None, False)},
    "LTPC": {
        1: ("ltp", DOUBLE, None, False),
        2: ("ltt", INT64, None, False),
        3: ("ltq", INT64, None, False),
        4: ("cp", DOUBLE, None, False),
        5: ("iep", MSG, "DoubleValue", False),
    },
    "Quote": {
        1: ("bidQ", INT64, None, False),
        2: ("bidP", DOUBLE, None, False),
        3: ("askQ", INT64, None, False),
        4: ("askP", DOUBLE, None, False),
    },
    "MarketLevel": {1: ("bidAskQuote", MSG, "Quote", True)},
    "OptionGreeks": {
        1: ("delta", DOUBLE, None, False),
        2: ("theta", DOUBLE, None, False),
        3: ("gamma", DOUBLE, None, False),
        4: ("vega", DOUBLE, None, False),
        5: ("rho", DOUBLE, None, False),
    },
    "OHLC": {
        1: ("interval", STRING, None, False),
        2: ("open", DOUBLE, None, False),
        3: ("high", DOUBLE, None, False),
        4: ("low", DOUBLE, None, False),
        5: ("close", DOUBLE, None, False),
        6: ("vol", INT64, None, False),
        7: ("ts", INT64, None, False),
    },
    "MarketOHLC": {1: ("ohlc", MSG, "OHLC", True)},
    "MarketFullFeed": {
        1: ("ltpc", MSG, "LTPC", False),
        2: ("marketLevel", MSG, "MarketLevel", False),
        3: ("optionGreeks", MSG, "OptionGreeks", False),
        4: ("marketOHLC", MSG, "MarketOHLC", False),
        5: ("atp", DOUBLE, None, False),
        6: ("vtt", INT64, None, False),
        7: ("oi", DOUBLE, None, False),
        8: ("iv", DOUBLE, None, False),
        9: ("tbq", DOUBLE, None, False),
        10: ("tsq", DOUBLE, None, False),
        11: ("iep", DOUBLE, None, False),
        12: ("rp", DOUBLE, None, False),
        13: ("ieq", INT64, None, False),
        14: ("iiqTotal", INT64, None, False),
        15: ("iiqM", INT64, None, False),
        16: ("casEligible", BOOL, None, False),
    },
    "IndexFullFeed": {
        1: ("ltpc", MSG, "LTPC", False),
        2: ("marketOHLC", MSG, "MarketOHLC", False),
    },
    "FullFeed": {
        1: ("marketFF", MSG, "MarketFullFeed", False),
        2: ("indexFF", MSG, "IndexFullFeed", False),
    },
    "FirstLevelWithGreeks": {
        1: ("ltpc", MSG, "LTPC", False),
        2: ("firstDepth", MSG, "Quote", False),
        3: ("optionGreeks", MSG, "OptionGreeks", False),
        4: ("vtt", INT64, None, False),
        5: ("oi", DOUBLE, None, False),
        6: ("iv", DOUBLE, None, False),
    },
    "Feed": {
        1: ("ltpc", MSG, "LTPC", False),
        2: ("fullFeed", MSG, "FullFeed", False),
        3: ("firstLevelWithGreeks", MSG, "FirstLevelWithGreeks", False),
        4: ("requestMode", ENUM, None, False),
    },
    "StatusInfo": {1: ("status", STRING, None, False), 2: ("updatedTime", INT64, None, False)},
    "SegmentStatusEntry": {1: ("key", STRING, None, False), 2: ("value", ENUM, None, False)},
    "StatusInfoEntry": {1: ("key", STRING, None, False), 2: ("value", MSG, "StatusInfo", False)},
    "MarketInfo": {
        1: ("segmentStatus", MSG, "SegmentStatusEntry", True),
        2: ("casMarketStatus", MSG, "StatusInfoEntry", True),
        3: ("preOpenSessionStatus", MSG, "StatusInfoEntry", True),
    },
    "FeedsEntry": {1: ("key", STRING, None, False), 2: ("value", MSG, "Feed", False)},
    "FeedResponse": {
        1: ("type", ENUM, None, False),
        2: ("feeds", MSG, "FeedsEntry", True),
        3: ("currentTs", INT64, None, False),
        4: ("marketInfo", MSG, "MarketInfo", False),
    },
}

FEED_TYPES = {0: "initial_feed", 1: "live_feed", 2: "market_info"}
REQUEST_MODES = {0: "ltpc", 1: "full_d5", 2: "option_greeks", 3: "full_d30"}
MARKET_STATUS = {
    0: "PRE_OPEN_START",
    1: "PRE_OPEN_END",
    2: "NORMAL_OPEN",
    3: "NORMAL_CLOSE",
    4: "CLOSING_START",
    5: "CLOSING_END",
}

# Map-typed fields are decoded into dicts.
MAP_FIELDS = {
    ("FeedResponse", "feeds"),
    ("MarketInfo", "segmentStatus"),
    ("MarketInfo", "casMarketStatus"),
    ("MarketInfo", "preOpenSessionStatus"),
}


class ProtoDecodeError(ValueError):
    pass


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise ProtoDecodeError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise ProtoDecodeError("varint too long")


def decode(buf: bytes, message: str = "FeedResponse") -> dict[str, Any]:
    schema = SCHEMA[message]
    out: dict[str, Any] = {}
    pos, end = 0, len(buf)
    while pos < end:
        tag, pos = _varint(buf, pos)
        field_no, wire = tag >> 3, tag & 7
        if wire == 0:
            raw, pos = _varint(buf, pos)
            value: Any = raw
        elif wire == 1:
            if pos + 8 > end:
                raise ProtoDecodeError("truncated fixed64")
            value = buf[pos : pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = _varint(buf, pos)
            if pos + length > end:
                raise ProtoDecodeError("truncated length-delimited field")
            value = buf[pos : pos + length]
            pos += length
        elif wire == 5:
            value = buf[pos : pos + 4]
            pos += 4
        else:
            raise ProtoDecodeError(f"unsupported wire type {wire}")

        spec = schema.get(field_no)
        if spec is None:
            continue  # unknown field (forward compatible)
        name, ftype, sub, repeated = spec
        if ftype == DOUBLE:
            parsed: Any = struct.unpack("<d", value)[0] if isinstance(value, bytes) else float(value)
        elif ftype == INT64:
            parsed = value - (1 << 64) if value >= (1 << 63) else value
        elif ftype == BOOL:
            parsed = bool(value)
        elif ftype == ENUM:
            parsed = value
        elif ftype == STRING:
            parsed = bytes(value).decode("utf-8", errors="replace")
        else:
            parsed = decode(bytes(value), sub or "")
        if repeated:
            out.setdefault(name, []).append(parsed)
        else:
            out[name] = parsed

    for msg_name, field_name in MAP_FIELDS:
        if msg_name == message and isinstance(out.get(field_name), list):
            out[field_name] = {e.get("key", ""): e.get("value") for e in out[field_name]}
    return out


# --- Encoder (used by tests and the local mock server to build realistic frames) -------


def _enc_varint(v: int) -> bytes:
    if v < 0:
        v += 1 << 64
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode(obj: dict[str, Any], message: str = "FeedResponse") -> bytes:
    schema = SCHEMA[message]
    by_name = {spec[0]: (num, spec) for num, spec in schema.items()}
    out = bytearray()
    for name, value in obj.items():
        if name not in by_name or value is None:
            continue
        num, (_, ftype, sub, repeated) = by_name[name]
        if (message, name) in MAP_FIELDS and isinstance(value, dict):
            value = [{"key": k, "value": v} for k, v in value.items()]
        items = value if repeated else [value]
        for item in items:
            if ftype == DOUBLE:
                out += _enc_varint(num << 3 | 1) + struct.pack("<d", float(item))
            elif ftype in (INT64, ENUM):
                out += _enc_varint(num << 3) + _enc_varint(int(item))
            elif ftype == BOOL:
                out += _enc_varint(num << 3) + _enc_varint(1 if item else 0)
            elif ftype == STRING:
                data = str(item).encode()
                out += _enc_varint(num << 3 | 2) + _enc_varint(len(data)) + data
            else:
                data = encode(item, sub or "")
                out += _enc_varint(num << 3 | 2) + _enc_varint(len(data)) + data
    return bytes(out)
