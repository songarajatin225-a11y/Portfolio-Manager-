"""Provider payload parsing, protobuf decoding, validation, calendar, resampling and candle building."""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.analytics.resample import resample_bars
from app.marketdata import upstox_proto
from app.marketdata.angelone import parse_binary_tick, parse_scrip
from app.marketdata.calendar import IST, next_upstox_token_expiry, previous_trading_day, session_state
from app.marketdata.candle_builder import CandleBuilder
from app.marketdata.mapping import extract_items, get_path, map_quote
from app.marketdata.types import CandleBar, Quote, validate_candle, validate_quote
from app.marketdata.upstox import feed_to_quotes, market_info_to_status, parse_instrument, parse_rest_quote

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 17, 9, 45, tzinfo=UTC)


# --- Upstox protobuf (fixture encoded by the official protobuf runtime) ------------------


def test_upstox_feed_fixture_decodes_to_quotes() -> None:
    decoded = upstox_proto.decode((FIXTURES / "upstox_feed_full.bin").read_bytes())
    assert decoded["type"] == 1
    quotes = {q.provider_key: q for q in feed_to_quotes(decoded, NOW)}
    assert set(quotes) == {"NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50", "BSE_EQ|INE009A01021"}

    eq = quotes["NSE_EQ|INE002A01018"]
    assert eq.ltp == pytest.approx(1523.45)
    assert eq.prev_close == pytest.approx(1510.10)
    assert eq.change == pytest.approx(13.35)
    assert eq.change_pct == pytest.approx(13.35 / 1510.10 * 100)
    assert (eq.open, eq.high, eq.low) == (1512.0, 1530.0, 1505.5)
    assert eq.volume == 4567890 and eq.vwap == pytest.approx(1519.87)
    assert len(eq.depth_buy) == 5 and len(eq.depth_sell) == 5
    assert eq.bid == pytest.approx(1523.40) and eq.ask == pytest.approx(1523.50)
    assert eq.bid_qty == 100 and eq.ask_qty == 90
    assert eq.last_trade_time == datetime.fromtimestamp(1763372699, UTC)

    idx = quotes["NSE_INDEX|Nifty 50"]
    assert idx.ltp == pytest.approx(25987.65) and idx.high == pytest.approx(26010.3) and idx.volume is None

    ltpc = quotes["BSE_EQ|INE009A01021"]
    assert ltpc.ltp == pytest.approx(1432.1) and ltpc.change_pct == pytest.approx((1432.1 - 1440) / 1440 * 100)


def test_upstox_market_info_and_roundtrip() -> None:
    info = upstox_proto.decode((FIXTURES / "upstox_market_info.bin").read_bytes())
    nse = market_info_to_status(info, "NSE")
    bse = market_info_to_status(info, "BSE")
    assert nse is not None and nse.status == "open" and nse.detail == "NORMAL_OPEN"
    assert bse is not None and bse.status == "closed"

    original = upstox_proto.decode((FIXTURES / "upstox_feed_full.bin").read_bytes())
    assert upstox_proto.decode(upstox_proto.encode(original)) == original


def test_upstox_proto_rejects_truncated_frames() -> None:
    data = (FIXTURES / "upstox_feed_full.bin").read_bytes()
    # the last 7 bytes are exactly the currentTs field, so cut deeper into the feeds map entry
    with pytest.raises(upstox_proto.ProtoDecodeError):
        upstox_proto.decode(data[:-12])


def test_upstox_rest_quote_and_instruments() -> None:
    item = {
        "ohlc": {"open": 2900.0, "high": 2950.5, "low": 2890.1, "close": 2940.0, "volume": 0, "ts": 0},
        "depth": {
            "buy": [{"quantity": 10, "price": 2939.9, "orders": 2}],
            "sell": [{"quantity": 7, "price": 2940.1, "orders": 1}],
        },
        "timestamp": "2026-11-17T15:14:21.312+05:30",
        "instrument_token": "NSE_EQ|INE002A01018",
        "symbol": "RELIANCE",
        "last_price": 2940.0,
        "volume": 1234567,
        "average_price": 2925.3,
        "oi": 0,
        "net_change": 20.0,
        "total_buy_quantity": 1000,
        "total_sell_quantity": 900,
        "lower_circuit_limit": 2628.0,
        "upper_circuit_limit": 3212.0,
        "last_trade_time": "1763372661000",
        "prev_close_price": 2920.0,
        "year_high": 3200.0,
        "year_low": 2200.0,
    }
    q = parse_rest_quote(item, NOW)
    assert q is not None
    assert q.prev_close == 2920.0 and q.change == 20.0 and q.change_pct == pytest.approx(20 / 2920 * 100)
    assert q.vwap == 2925.3 and q.bid == 2939.9 and q.ask == 2940.1 and q.high_52w == 3200.0

    eq = parse_instrument(
        {
            "segment": "NSE_EQ",
            "name": "RELIANCE INDUSTRIES LTD",
            "exchange": "NSE",
            "isin": "INE002A01018",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE002A01018",
            "lot_size": 1,
            "exchange_token": "2885",
            "tick_size": 10.0,
            "trading_symbol": "RELIANCE",
        },
        include_fo=False,
    )
    assert eq is not None and eq.symbol == "RELIANCE" and eq.isin == "INE002A01018" and eq.tick_size == 0.1
    idx = parse_instrument(
        {
            "segment": "NSE_INDEX",
            "name": "Nifty 50",
            "exchange": "NSE",
            "instrument_key": "NSE_INDEX|Nifty 50",
            "instrument_type": "INDEX",
            "trading_symbol": "NIFTY",
        },
        include_fo=False,
    )
    assert idx is not None and idx.is_index and idx.symbol == "Nifty 50"
    fo = {
        "segment": "NSE_FO",
        "name": "NIFTY",
        "instrument_key": "NSE_FO|12345",
        "instrument_type": "CE",
        "trading_symbol": "NIFTY 26000 CE 27 NOV 25",
        "strike_price": 26000.0,
        "expiry": 1764201600000,
        "lot_size": 75,
    }
    assert parse_instrument(fo, include_fo=False) is None
    opt = parse_instrument(fo, include_fo=True)
    assert opt is not None and opt.option_type == "CE" and opt.strike == 26000.0 and opt.expiry is not None


# --- Angel One SmartAPI binary packets (layout from smartapi-python 1.5.5) ----------------


def build_angel_snap(token: str = "2885", exchange_type: int = 1) -> bytes:  # noqa: S107 - instrument token
    buf = bytearray(379)
    buf[0], buf[1] = 3, exchange_type
    tok = token.encode()
    buf[2 : 2 + len(tok)] = tok
    struct.pack_into("<qqq", buf, 27, 99, 1763372699000, 152345)  # seq, exch ts (ms), LTP paise
    struct.pack_into("<qqq", buf, 51, 25, 151987, 4567890)  # LTQ, ATP, volume
    struct.pack_into("<dd", buf, 75, 345678.0, 298765.0)
    struct.pack_into("<qqqq", buf, 91, 151200, 153000, 150550, 151010)  # O H L prevC
    struct.pack_into("<qq", buf, 123, 1763372699, 0)  # LTT, OI
    for i in range(5):
        struct.pack_into("<HqqH", buf, 147 + i * 20, 0, 100 * (i + 1), 152340 - i * 5, 3)
        struct.pack_into("<HqqH", buf, 147 + (i + 5) * 20, 1, 90 * (i + 1), 152350 + i * 5, 2)
    struct.pack_into("<qqqq", buf, 347, 167500, 137000, 320000, 220000)
    return bytes(buf)


def test_angel_snapquote_packet() -> None:
    q = parse_binary_tick(build_angel_snap(), NOW)
    assert q is not None
    assert q.provider_key == "NSE:2885"
    assert q.ltp == pytest.approx(1523.45)
    assert q.prev_close == pytest.approx(1510.10) and q.vwap == pytest.approx(1519.87)
    assert (q.open, q.high, q.low) == (1512.0, 1530.0, 1505.5)
    assert q.volume == 4567890
    assert [d.price for d in q.depth_buy][:2] == [1523.40, 1523.35]
    assert q.depth_sell[0].price == pytest.approx(1523.50) and q.ask_qty == 90
    assert q.upper_circuit == 1675.0 and q.high_52w == 3200.0
    assert q.exchange_ts == datetime.fromtimestamp(1763372699, UTC)
    assert q.last_trade_time == datetime.fromtimestamp(1763372699, UTC)


def test_angel_ltp_packet_and_short_packet() -> None:
    buf = bytearray(51)
    buf[0], buf[1] = 1, 3
    buf[2:7] = b"500325"[:5]
    struct.pack_into("<qqq", buf, 27, 1, 1763372699000, 294012)
    q = parse_binary_tick(bytes(buf), NOW)
    assert q is not None and q.provider_key.startswith("BSE:") and q.ltp == pytest.approx(2940.12)
    assert parse_binary_tick(b"\x01\x01short", NOW) is None


def test_angel_scrip_master_rows() -> None:
    eq = parse_scrip(
        {
            "token": "2885",
            "symbol": "RELIANCE-EQ",
            "name": "RELIANCE",
            "expiry": "",
            "strike": "-1.000000",
            "lotsize": "1",
            "instrumenttype": "",
            "exch_seg": "NSE",
            "tick_size": "10.000000",
        },
        False,
    )
    assert eq is not None and eq.symbol == "RELIANCE" and eq.provider_key == "NSE:2885" and eq.tick_size == 0.1
    idx = parse_scrip(
        {
            "token": "99926000",
            "symbol": "Nifty 50",
            "name": "NIFTY",
            "instrumenttype": "AMXIDX",
            "exch_seg": "NSE",
            "lotsize": "1",
            "tick_size": "0",
        },
        False,
    )
    assert idx is not None and idx.is_index and idx.symbol == "Nifty 50"
    opt_row = {
        "token": "43210",
        "symbol": "NIFTY27NOV2526000CE",
        "name": "NIFTY",
        "expiry": "27NOV2025",
        "strike": "2600000.000000",
        "lotsize": "75",
        "instrumenttype": "OPTIDX",
        "exch_seg": "NFO",
        "tick_size": "5.000000",
    }
    assert parse_scrip(opt_row, False) is None
    opt = parse_scrip(opt_row, True)
    assert opt is not None and opt.strike == 26000.0 and opt.option_type == "CE" and opt.expiry == date(2025, 11, 27)
    assert parse_scrip({"token": "1", "symbol": "X-BE", "instrumenttype": "", "exch_seg": "NSE"}, False) is None


# --- Validation ---------------------------------------------------------------------------


def test_quote_validation() -> None:
    good = Quote(provider_key="k", ltp=100, high=101, low=99, received_at=NOW, source="t")
    assert validate_quote(good, NOW) == []
    assert validate_quote(Quote(provider_key="k", ltp=0, received_at=NOW, source="t"), NOW)
    assert validate_quote(Quote(provider_key="k", ltp=float("nan"), received_at=NOW, source="t"), NOW)
    assert "high is below low" in validate_quote(
        Quote(provider_key="k", ltp=100, high=90, low=95, received_at=NOW, source="t"), NOW
    )
    assert validate_quote(Quote(provider_key="k", ltp=150, high=101, low=99, received_at=NOW, source="t"), NOW)
    future = Quote(provider_key="k", ltp=100, exchange_ts=NOW + timedelta(hours=1), received_at=NOW, source="t")
    assert "timestamp is in the future" in validate_quote(future, NOW)
    assert validate_quote(Quote(provider_key="k", ltp=100, volume=-5, received_at=NOW, source="t"), NOW)


def test_candle_validation() -> None:
    assert validate_candle(CandleBar(NOW, 10, 11, 9, 10.5, 100)) == []
    assert validate_candle(CandleBar(NOW, 10, 9.5, 9, 10.5, 100))  # high below close
    assert validate_candle(CandleBar(NOW, 10, 11, 9, 10.5, -1))
    assert validate_candle(CandleBar(NOW, 0, 11, 9, 10.5, 1))


# --- Calendar -----------------------------------------------------------------------------


def ist(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_session_states() -> None:
    assert session_state(ist(2026, 11, 17, 9, 5)) == "pre_open"  # Tuesday
    assert session_state(ist(2026, 11, 17, 9, 15)) == "open"
    assert session_state(ist(2026, 11, 17, 15, 29)) == "open"
    assert session_state(ist(2026, 11, 17, 15, 45)) == "post_close"
    assert session_state(ist(2026, 11, 17, 20, 0)) == "closed"
    assert session_state(ist(2026, 11, 21, 11, 0)) == "weekend"
    assert session_state(ist(2026, 11, 17, 11, 0), {date(2026, 11, 17)}) == "holiday"
    assert previous_trading_day(date(2026, 11, 16)) == date(2026, 11, 13)  # Monday -> Friday


def test_upstox_token_expiry() -> None:
    assert next_upstox_token_expiry(ist(2026, 11, 17, 10, 0)) == ist(2026, 11, 18, 3, 30).astimezone(UTC)
    assert next_upstox_token_expiry(ist(2026, 11, 17, 2, 0)) == ist(2026, 11, 17, 3, 30).astimezone(UTC)


# --- Resampling & candle building -------------------------------------------------------------


def test_resample_weekly_and_intraday_alignment() -> None:
    days = [
        CandleBar(ist(2026, 11, d, 0, 0).astimezone(UTC), 100 + d, 110 + d, 90 + d, 105 + d, 1000)
        for d in (16, 17, 18, 19, 20, 23)
    ]
    weekly = resample_bars(days, "1w")
    assert len(weekly) == 2
    assert weekly[0].open == 116 and weekly[0].close == 125 and weekly[0].high == 130 and weekly[0].volume == 5000
    minutes = [CandleBar(ist(2026, 11, 17, 9, 15 + i).astimezone(UTC), 10, 11, 9, 10 + i, 1) for i in range(10)]
    five = resample_bars(minutes, "5m")
    assert [b.ts.astimezone(IST).strftime("%H:%M") for b in five] == ["09:15", "09:20"]
    assert five[0].close == 14 and five[0].volume == 5


def test_candle_builder_rolls_minutes_and_uses_volume_deltas() -> None:
    b = CandleBuilder()

    def q(t: datetime, ltp: float, vol: float) -> Quote:
        return Quote(provider_key="k", instrument_id=1, ltp=ltp, volume=vol, exchange_ts=t, received_at=t, source="s")

    t0 = datetime(2026, 11, 17, 4, 0, 5, tzinfo=UTC)
    assert b.on_quote(q(t0, 100, 1000)) == []
    b.on_quote(q(t0 + timedelta(seconds=20), 102, 1500))
    b.on_quote(q(t0 + timedelta(seconds=40), 99, 1800))
    done = b.on_quote(q(t0 + timedelta(seconds=60), 101, 2000))
    assert len(done) == 1
    bar = done[0].bar
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100, 102, 99, 99, 800)
    assert b.on_quote(q(t0 - timedelta(seconds=10), 50, 2100)) == []  # late tick ignored
    flushed = b.flush_idle(t0 + timedelta(minutes=5))
    # 200 = day volume traded since the previous minute's last tick (2000 - 1800)
    assert len(flushed) == 1 and flushed[0].bar.volume == 200 and flushed[0].bar.close == 101


# --- Custom provider field mapping --------------------------------------------------------------


def test_mapping_helpers() -> None:
    body = {"data": {"RELIANCE": {"q": {"last": "101.5", "pc": 100}, "t": 1763372699000}}}
    items = extract_items(body, "data")
    assert items[0][0] == "RELIANCE"
    q = map_quote(
        items[0][1],
        {"ltp": "q.last", "prev_close": "q.pc", "timestamp": "t"},
        source="custom:x",
        received_at=NOW,
        key_override=items[0][0],
        delayed=True,
    )
    assert q is not None and q.ltp == 101.5 and q.change_pct == pytest.approx(1.5) and q.provider_delayed
    assert get_path({"a": [{"b": 2}]}, "a.0.b") == 2 and get_path({"a": 1}, "a.b") is None
