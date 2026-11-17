"""Fundamental ratio maths (hand-computed expectations) and input validation."""

from __future__ import annotations

from datetime import date

import pytest

from app.analytics.fundamentals import Period, compute_ratios, flow_basis, validate_period


def q(end: date, **v: float) -> Period:
    return Period("Q", end, "test-filing", reported_at=end.replace(day=28) if end.month != 2 else end, values=v)


QUARTERS = [
    q(
        date(2026, 9, 30),
        revenue=1100,
        ebitda=220,
        ebit=180,
        net_profit=120,
        eps=12.0,
        operating_cash_flow=150,
        capex=40,
        dividend_per_share=2,
        total_equity=2000,
        total_debt=500,
        total_assets=4000,
        current_liabilities=800,
        shares_outstanding=10,
    ),
    q(
        date(2026, 6, 30),
        revenue=1000,
        ebitda=200,
        ebit=160,
        net_profit=100,
        eps=10.0,
        operating_cash_flow=130,
        capex=30,
        dividend_per_share=0,
    ),
    q(
        date(2026, 3, 31),
        revenue=950,
        ebitda=190,
        ebit=150,
        net_profit=95,
        eps=9.5,
        operating_cash_flow=120,
        capex=30,
        dividend_per_share=3,
    ),
    q(
        date(2025, 12, 31),
        revenue=950,
        ebitda=190,
        ebit=150,
        net_profit=85,
        eps=8.5,
        operating_cash_flow=100,
        capex=20,
        dividend_per_share=0,
    ),
    # prior-year quarters for growth
    q(date(2025, 9, 30), revenue=900, net_profit=80, eps=8.0, total_equity=1800),
    q(date(2025, 6, 30), revenue=850, net_profit=75, eps=7.5),
    q(date(2025, 3, 31), revenue=800, net_profit=70, eps=7.0),
    q(date(2024, 12, 31), revenue=750, net_profit=75, eps=7.5),
]


def test_ttm_ratios_hand_computed() -> None:
    out = compute_ratios(QUARTERS, price=500.0, price_source="test", price_as_of="2026-11-17T10:00:00+05:30")
    r = out["ratios"]
    # TTM: revenue 4000, EBITDA 800, EBIT 640, profit 400, EPS 40, OCF 500, capex 120, DPS 5
    assert r["eps"]["value"] == 40 and r["eps"]["period"] == "TTM to 2026-09-30"
    assert r["pe"]["value"] == pytest.approx(12.5)
    assert r["ebitda_margin"]["value"] == pytest.approx(20.0)
    assert r["net_margin"]["value"] == pytest.approx(10.0)
    assert r["free_cash_flow"]["value"] == pytest.approx(380)
    assert r["earnings_quality"]["value"] == pytest.approx(1.25)
    assert r["dividend_yield"]["value"] == pytest.approx(1.0)
    # ROE on average of opening (1800) and closing (2000) equity: 400 / 1900
    assert r["roe"]["value"] == pytest.approx(400 / 1900 * 100, abs=1e-4)  # ratios are rounded to 4 dp
    # ROCE: EBIT 640 / (4000 - 800)
    assert r["roce"]["value"] == pytest.approx(20.0)
    assert r["debt_to_equity"]["value"] == pytest.approx(0.25)
    assert r["pb"]["value"] == pytest.approx(500 / (2000 / 10))
    assert r["market_cap"]["value"] == pytest.approx(5000)
    # growth: TTM 4000 vs prior TTM 3300; profit 400 vs 300
    assert r["revenue_growth"]["value"] == pytest.approx((4000 / 3300 - 1) * 100, abs=1e-4)
    assert r["profit_growth"]["value"] == pytest.approx((400 / 300 - 1) * 100, abs=1e-4)
    assert r["pe"]["source"] == "test-filing" and r["pe"]["reported_at"] is not None
    assert r["pe"]["formula"] == "Price / EPS"


def test_negative_earnings_and_missing_price_are_explained() -> None:
    loss = [
        Period(
            "A",
            date(2026, 3, 31),
            "ar",
            values={"net_profit": -50, "eps": -5, "revenue": 1000, "shares_outstanding": 10, "total_equity": 300},
        )
    ]
    r = compute_ratios(loss, price=100)["ratios"]
    assert r["pe"]["value"] is None and "negative" in r["pe"]["reason"]
    assert r["pe"]["period"] == "FY ending 2026-03-31"
    r2 = compute_ratios(loss, price=None)["ratios"]
    assert r2["pb"]["value"] is None and r2["pb"]["reason"] == "No current price available"


def test_non_consecutive_quarters_fall_back_to_annual() -> None:
    periods = [
        q(date(2026, 9, 30), revenue=1),
        q(date(2026, 3, 31), revenue=1),
        q(date(2025, 12, 31), revenue=1),
        q(date(2025, 9, 30), revenue=1),
        Period("A", date(2026, 3, 31), "ar", values={"revenue": 3600}),
    ]
    basis = flow_basis(periods)
    assert basis is not None and basis.label == "FY ending 2026-03-31"


def test_validation_rejects_bad_inputs_and_excludes_them() -> None:
    bad = Period(
        "Q", date(2026, 6, 30), "x", values={"ebit": 300, "ebitda": 200, "revenue": -1, "shares_outstanding": 0}
    )
    errs = validate_period(bad, today=date(2026, 11, 17))
    assert any("ebit exceeds ebitda" in e for e in errs)
    assert any("revenue cannot be negative" in e for e in errs)
    assert any("shares_outstanding" in e for e in errs)
    future = Period("A", date(2027, 3, 31), "x")
    assert "period_end is in the future" in validate_period(future, today=date(2026, 11, 17))
    eps_mismatch = Period("A", date(2026, 3, 31), "x", values={"net_profit": 100, "eps": 30, "shares_outstanding": 10})
    assert any("inconsistent" in e for e in validate_period(eps_mismatch, today=date(2026, 11, 17)))

    bad.validation_errors = errs
    out = compute_ratios([bad], price=100)
    assert out["periods_rejected"] == 1 and out["ratios"] == {} and "No validated" in out["note"]
