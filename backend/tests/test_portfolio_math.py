"""Portfolio accounting, performance and risk maths, checked against hand calculations."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.analytics import journal as ja
from app.analytics import portfolio as pa
from app.analytics import risk as ra


def T(iid, side, qty, price, d, charges=0.0, cat=""):  # noqa: ANN001, ANN201, N802
    return pa.Txn(iid, side, qty, price, charges, d, cat)


def test_fifo_realised_and_cost_basis() -> None:
    txns = [
        T(1, "BUY", 10, 100, date(2024, 1, 1), charges=10),  # unit cost 101
        T(1, "BUY", 10, 120, date(2024, 2, 1)),
        T(1, "SELL", 15, 130, date(2024, 3, 1), charges=15),
        T(1, "DIVIDEND", 5, 4, date(2024, 4, 1)),
    ]
    pos = pa.build_positions(txns)[1]
    # cost of the 15 sold = 10 x 101 + 5 x 120 = 1610; proceeds = 1950 - 15
    assert pos.realized_pnl == pytest.approx(1935 - 1610)
    assert pos.quantity == 5
    assert pos.avg_cost == pytest.approx(120)
    assert pos.dividends == pytest.approx(20)


def test_oversell_is_rejected() -> None:
    with pytest.raises(pa.LedgerError):
        pa.build_positions([T(1, "BUY", 5, 100, date(2024, 1, 1)), T(1, "SELL", 6, 110, date(2024, 1, 2))])


def test_xirr_matches_closed_form() -> None:
    flows = [(date(2020, 1, 1), -1000.0), (date(2021, 1, 1), 1100.0)]  # 366 days
    assert pa.xirr(flows) == pytest.approx(1.1 ** (365 / 366) - 1, abs=1e-6)
    assert pa.xirr([(date(2020, 1, 1), -1000.0)]) is None
    assert pa.xirr([(date(2020, 1, 1), 100.0), (date(2021, 1, 1), 100.0)]) is None


def test_summary_values_and_concentration() -> None:
    txns = [T(1, "BUY", 10, 100, date(2024, 1, 1)), T(2, "BUY", 30, 100, date(2024, 1, 1))]
    positions = pa.build_positions(txns)
    prices = {1: {"price": 110.0, "data_status": "SIMULATED"}, 2: {"price": 90.0, "data_status": "SIMULATED"}}
    meta = {1: {"symbol": "AAA", "sector": "IT"}, 2: {"symbol": "BBB", "sector": "Banks"}}
    s = pa.summarize(positions, prices, meta, pa.cash_flows(txns), date(2025, 1, 1))
    assert s["current_value"] == pytest.approx(1100 + 2700)
    assert s["unrealized_pnl"] == pytest.approx(100 - 300)
    weights = sorted(h["weight"] for h in s["holdings"])
    assert weights == pytest.approx([1100 / 3800, 2700 / 3800])
    hhi = (1100 / 3800) ** 2 + (2700 / 3800) ** 2
    assert s["concentration"]["hhi"] == pytest.approx(hhi, abs=1e-4)
    assert s["concentration"]["largest"]["name"] == "BBB"
    assert s["xirr_pct"] is not None and s["xirr_pct"] < 0


def test_unpriced_holdings_are_valued_at_cost_and_flagged() -> None:
    txns = [T(1, "BUY", 10, 100, date(2024, 1, 1))]
    s = pa.summarize(pa.build_positions(txns), {}, {1: {"symbol": "AAA"}}, pa.cash_flows(txns), date(2024, 6, 1))
    assert s["current_value"] == 1000
    assert s["missing_prices"] == ["AAA"]
    assert s["note"]


def test_time_weighted_return_ignores_deposits() -> None:
    idx = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
    closes = pd.DataFrame({1: [100.0, 110.0, 110.0, 99.0]}, index=idx)
    txns = [T(1, "BUY", 10, 100, date(2024, 1, 1)), T(1, "BUY", 10, 110, date(2024, 1, 3))]
    series = pa.valuation_series(txns, closes)
    assert list(series["value"]) == [1000, 1100, 2200, 1980]
    assert list(series["flow"]) == [1000, 0, 1100, 0]
    perf = pa.performance(series)
    # +10%, 0%, -10% -> TWR = 1.1 x 0.9 - 1 = -1% (money-weighted would differ)
    assert perf["twr_pct"] == pytest.approx(-1.0)
    assert perf["max_drawdown_pct"] == pytest.approx(-10.0)


def test_sell_and_dividend_flows() -> None:
    idx = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    closes = pd.DataFrame({1: [100.0, 100.0, 100.0]}, index=idx)
    txns = [
        T(1, "BUY", 10, 100, date(2024, 1, 1)),
        T(1, "SELL", 5, 100, date(2024, 1, 2), charges=5),
        T(1, "DIVIDEND", 5, 2, date(2024, 1, 3)),
    ]
    series = pa.valuation_series(txns, closes)
    assert list(series["flow"]) == [1000, -495, -10]
    perf = pa.performance(series)
    # day 2 loses the 5 charge on 1000; day 3 earns the 10 dividend on 500
    assert perf["twr_pct"] == pytest.approx(((1 - 0.005) * (1 + 0.02) - 1) * 100)


def test_rebalance_equal_weight() -> None:
    holdings = [
        {"symbol": "AAA", "market_value": 7000, "price": 100},
        {"symbol": "BBB", "market_value": 3000, "price": 50},
    ]
    r = pa.rebalance(holdings, {}, threshold_pp=5)
    assert r["basis"] == "equal weight"
    by = {s["symbol"]: s for s in r["suggestions"]}
    assert by["AAA"]["action"] == "SELL" and by["AAA"]["value"] == pytest.approx(2000)
    assert by["BBB"]["action"] == "BUY" and by["BBB"]["quantity"] == 40
    assert any("AAA" in w for w in r["warnings"])


def test_var_matches_empirical_quantile() -> None:
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0005, 0.01, 500))
    out = ra.value_at_risk(r, 1_000_000, confidences=(0.95,), horizons=(1,))
    row = out["table"][0]
    assert row["historical_pct"] == pytest.approx(-np.quantile(r, 0.05) * 100, abs=1e-3)
    assert row["parametric_pct"] == pytest.approx((-(r.mean() + r.std(ddof=1) * -1.6448536)) * 100, abs=1e-3)
    assert row["expected_shortfall_pct"] >= row["historical_pct"]
    assert "methodology" in out and "normally distributed" in out["methodology"]
    assert ra.value_at_risk(r.head(10), 1)["available"] is False


def test_position_size_and_risk_reward() -> None:
    out = ra.position_size(capital=1_000_000, risk_pct=1, entry=500, stop=480)
    assert out["quantity"] == 500 and out["risk_amount"] == 10_000
    capped = ra.position_size(1_000_000, 1, 500, 480, max_position_pct=10)
    assert capped["quantity"] == 200 and capped["capped_by_max_position"]
    assert ra.position_size(100_000, 1, 100, 99, lot_size=75)["quantity"] == 975
    assert ra.risk_reward(100, 95, 115) == 3.0
    with pytest.raises(ValueError):
        ra.position_size(1000, 1, 100, 100)


def test_correlation_and_diversification() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(0, 0.01, 200)
    rets = pd.DataFrame({1: a, 2: a + rng.normal(0, 0.001, 200), 3: rng.normal(0, 0.01, 200)})
    c = ra.correlation_matrix(rets, {1: "A", 2: "B", 3: "C"})
    assert c["labels"] == ["A", "B", "C"] and c["matrix"][0][1] > 0.95 and abs(c["matrix"][0][2]) < 0.3
    dr = ra.diversification_ratio({1: 0.5, 3: 0.5}, rets)
    assert dr is not None and dr > 1.2  # uncorrelated pair diversifies (≈ √2)


def _jt(i, pnl_per_share, *, stop=None, hours=24, emotion="", entry="2024-01-01 10:00", qty=10):  # noqa: ANN001, ANN202
    e = pd.Timestamp(entry, tz="Asia/Kolkata").to_pydatetime()
    return ja.JTrade(
        id=i,
        symbol="AAA",
        direction="LONG",
        status="CLOSED",
        entry_at=e,
        entry_price=100,
        quantity=qty,
        exit_at=e + pd.Timedelta(hours=hours).to_pytimedelta(),
        exit_price=100 + pnl_per_share,
        stop_loss=stop,
        strategy="S1",
        emotion=emotion,
    )


def test_journal_statistics() -> None:
    trades = [_jt(1, 10, stop=95), _jt(2, -5, stop=95), _jt(3, 20, stop=90), _jt(4, -10, stop=95)]
    s = ja.statistics(trades)
    assert s["trades"] == 4 and s["win_rate"] == 0.5
    assert s["profit_factor"] == pytest.approx((100 + 200) / (50 + 100))
    assert s["avg_r"] == pytest.approx(np.mean([2, -1, 2, -2]))
    assert s["max_drawdown"] == pytest.approx(-100)  # equity 100, 50, 250, 150
    assert s["by_strategy"][0]["strategy"] == "S1"


def test_journal_findings_detect_discipline_problems() -> None:
    trades = [
        _jt(1, -12, stop=95, hours=200),
        _jt(2, 5, hours=5),
        _jt(3, -3, emotion="FOMO", hours=100),
        _jt(4, -2, emotion="FOMO", hours=150),
        _jt(5, -1, emotion="FOMO", hours=120),
        _jt(6, 4, hours=6),
        _jt(7, 3, hours=4),
    ]
    codes = {f["code"] for f in ja.behaviour_findings(trades)}
    assert {"stop_not_respected", "no_stop_loss", "holding_losers", "emotion_fomo"} <= codes
    revenge = [_jt(1, -5, stop=95, entry="2024-01-01 10:00", hours=1), _jt(2, 1, entry="2024-01-01 11:30", qty=30)]
    assert "revenge_trading" in {f["code"] for f in ja.behaviour_findings(revenge)}
