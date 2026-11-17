"""Backtest engine: execution timing, stop handling, costs and bias controls."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.analytics import backtest as bt

NO_COSTS = bt.Costs(brokerage_pct=0, brokerage_cap=None, statutory_pct=0, slippage_bps=0)


def frame(closes, opens=None, highs=None, lows=None, start="2024-01-01"):  # noqa: ANN001, ANN201
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    o = np.asarray(opens if opens is not None else c, dtype=float)
    h = np.asarray(highs if highs is not None else np.maximum(o, c), dtype=float)
    lo = np.asarray(lows if lows is not None else np.minimum(o, c), dtype=float)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}, index=idx)


def cfg(strategy="custom", params=None, **kw):  # noqa: ANN001, ANN201
    base = dict(
        strategy=strategy,
        params=params or {},
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        initial_capital=100_000,
        sizing_method="percent_equity",
        sizing_value=100,
        max_positions=1,
        costs=NO_COSTS,
    )
    base.update(kw)
    return bt.Config(**base)


def test_signal_at_close_fills_next_open() -> None:
    closes = [100, 101, 102, 106, 107, 108, 109, 110]
    opens = [100, 100.5, 101.5, 103, 106.5, 107.5, 108.5, 109.5]
    df = frame(closes, opens)
    rule = {"entry": [{"left": "close", "op": ">", "right": 105}], "exit": []}
    res = bt.run(cfg(params=rule), {"AAA": df})
    t = res["trades"][0]
    assert t["entry_date"] == df.index[4].date().isoformat()  # signal on bar 3's close
    assert t["entry_price"] == pytest.approx(106.5)  # bar 4's open, not bar 3's close
    assert t["exit_reason"] == "end of test"


def test_future_bars_do_not_change_past_signals() -> None:
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    df = frame(closes)
    e1, x1 = bt.signals(df.iloc[:200], "ma_crossover", {"fast": 10, "slow": 30})
    e2, x2 = bt.signals(df, "ma_crossover", {"fast": 10, "slow": 30})
    assert (e1 == e2.iloc[:200]).all() and (x1 == x2.iloc[:200]).all()


def test_stop_fills_first_when_stop_and_target_hit_same_bar() -> None:
    # enter at bar 1 open (100); bar 2 touches both the 5% stop (95) and 10% target (110)
    df = frame([100, 100, 100, 100], opens=[100, 100, 98, 100], highs=[100, 100, 112, 100], lows=[100, 100, 90, 100])
    rule = {"entry": [{"left": "close", "op": ">=", "right": 100}], "exit": []}
    res = bt.run(cfg(params=rule, stop_loss_pct=5, take_profit_pct=10, end=date(2024, 1, 4)), {"AAA": df})
    t = res["trades"][0]
    assert t["exit_reason"] == "stop-loss"
    assert t["exit_price"] == pytest.approx(95)


def test_gap_through_stop_fills_at_open() -> None:
    df = frame([100, 100, 88, 88], opens=[100, 100, 90, 88], highs=[100, 100, 91, 88], lows=[100, 100, 87, 88])
    rule = {"entry": [{"left": "close", "op": ">=", "right": 100}], "exit": []}
    res = bt.run(cfg(params=rule, stop_loss_pct=5, end=date(2024, 1, 4)), {"AAA": df})
    assert res["trades"][0]["exit_price"] == pytest.approx(90)


def test_costs_and_slippage_reduce_returns() -> None:
    rng = np.random.default_rng(5)
    df = frame(100 * np.exp(np.cumsum(rng.normal(0.001, 0.015, 250))))
    params = {"fast": 5, "slow": 20}
    free = bt.run(cfg("ma_crossover", params), {"AAA": df})
    costly = bt.run(cfg("ma_crossover", params, costs=bt.Costs(slippage_bps=20)), {"AAA": df})
    assert free["metrics"]["trades"] == costly["metrics"]["trades"] > 0
    assert costly["metrics"]["final_equity"] < free["metrics"]["final_equity"]
    assert costly["metrics"]["total_costs"] > 0
    c = bt.Costs(brokerage_pct=0.03, brokerage_cap=20, statutory_pct=0.1)
    assert c.charges(200_000) == pytest.approx(20 + 200)  # brokerage capped at ₹20


def test_point_in_time_eligibility_blocks_entries() -> None:
    df = frame([100 + i for i in range(30)])
    rule = {"entry": [{"left": "close", "op": ">", "right": 0}], "exit": []}
    res = bt.run(cfg(params=rule), {"AAA": df}, eligible=lambda s, d: d >= date(2024, 1, 20))
    assert res["trades"][0]["entry_date"] >= "2024-01-22"


def test_delisted_position_closes_when_data_ends() -> None:
    a = frame([100 + i for i in range(10)])
    b = frame([50 + i for i in range(30)])
    rule = {"entry": [{"left": "close", "op": ">", "right": 0}], "exit": []}
    res = bt.run(cfg(params=rule, max_positions=2, sizing_value=50), {"AAA": a, "BBB": b})
    reasons = {t["symbol"]: t["exit_reason"] for t in res["trades"]}
    assert reasons["AAA"].startswith("data ended")


def test_momentum_rotates_into_strongest() -> None:
    n = 300
    frames = {f"S{i}": frame(100 * np.exp(np.linspace(0, 0.002 * i * n, n))) for i in range(1, 6)}
    res = bt.run(cfg("momentum", {"lookback": 60, "skip": 5, "top_n": 2}), frames)
    held = {t["symbol"] for t in res["trades"]}
    assert held <= {"S4", "S5"} and held


def test_metrics_and_benchmark() -> None:
    df = frame(np.linspace(100, 150, 260))
    rule = {"entry": [{"left": "close", "op": ">", "right": 0}], "exit": []}
    res = bt.run(cfg(params=rule), {"AAA": df}, benchmark=df["close"])
    m = res["metrics"]
    assert m["max_drawdown_pct"] == 0
    assert m["benchmark_return_pct"] == pytest.approx(50, abs=0.1)
    assert m["total_return_pct"] > 40
    assert res["equity_curve"][-1]["equity"] == m["final_equity"]


def test_invalid_rules_are_reported() -> None:
    df = frame([100] * 50)
    with pytest.raises(bt.BacktestError):
        bt.run(cfg(params={"entry": [{"left": "nonsense_5", "op": ">", "right": 1}]}), {"AAA": df})
    with pytest.raises(bt.BacktestError):
        bt.run(cfg(params={"entry": [{"left": "close", "op": "~", "right": 1}]}), {"AAA": df})
    with pytest.raises(bt.BacktestError):
        bt.run(cfg("ma_crossover", {"fast": 50, "slow": 20}), {"AAA": df})
