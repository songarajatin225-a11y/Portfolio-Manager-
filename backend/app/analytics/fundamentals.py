"""Fundamental ratio calculation from validated financial statement inputs.

Rules
* Values are in INR crore; ``shares_outstanding`` is in crore shares, so per-share values are INR.
* A period is used only if it passed validation (``validation_errors`` is empty).
* Trailing-twelve-month (TTM) flow figures need the four most recent quarters to be consecutive;
  otherwise the latest annual report is used. The basis is stated on every ratio.
* A ratio is returned only when every input exists and makes the ratio meaningful (e.g. no P/E on
  negative earnings); otherwise ``value`` is ``None`` with the reason.
* Every ratio carries its formula, period, source and reporting date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

FLOW_FIELDS = ("revenue", "ebitda", "ebit", "net_profit", "eps", "operating_cash_flow", "capex", "dividend_per_share")
STOCK_FIELDS = ("total_equity", "total_debt", "total_assets", "current_liabilities", "cash", "shares_outstanding")
NUMERIC_FIELDS = FLOW_FIELDS + STOCK_FIELDS


@dataclass
class Period:
    period_type: str  # Q | A
    period_end: date
    source: str
    reported_at: date | None = None
    statement_type: str = "consolidated"
    values: dict[str, float | None] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)

    def get(self, name: str) -> float | None:
        v = self.values.get(name)
        return None if v is None else float(v)


def validate_period(p: Period, today: date | None = None) -> list[str]:
    errors: list[str] = []
    today = today or date.today()
    if p.period_type not in ("Q", "A"):
        errors.append("period_type must be Q or A")
    if p.period_end > today:
        errors.append("period_end is in the future")
    if p.reported_at and p.reported_at < p.period_end:
        errors.append("reported_at is before period_end")
    for name in ("revenue", "total_assets", "total_debt", "current_liabilities", "cash", "capex", "dividend_per_share"):
        v = p.get(name)
        if v is not None and v < 0:
            errors.append(f"{name} cannot be negative")
    shares = p.get("shares_outstanding")
    if shares is not None and shares <= 0:
        errors.append("shares_outstanding must be positive")
    ebit, ebitda = p.get("ebit"), p.get("ebitda")
    if ebit is not None and ebitda is not None and ebit > ebitda + max(1.0, abs(ebitda) * 0.001):
        errors.append("ebit exceeds ebitda (depreciation cannot be negative)")
    eq, assets = p.get("total_equity"), p.get("total_assets")
    if eq is not None and assets is not None and eq > assets * 1.001:
        errors.append("total_equity exceeds total_assets")
    np_, eps = p.get("net_profit"), p.get("eps")
    if np_ is not None and eps not in (None, 0) and shares:
        implied = np_ / shares
        if abs(implied - eps) / abs(eps) > 0.35:
            errors.append(f"eps {eps} inconsistent with net_profit/shares ({implied:.2f})")
    return errors


def _consecutive_quarters(qs: list[Period]) -> bool:
    if len(qs) < 4:
        return False
    for a, b in zip(qs, qs[1:], strict=False):
        gap = (a.period_end - b.period_end).days
        if not 80 <= gap <= 100:
            return False
    return True


@dataclass
class Basis:
    label: str
    periods: list[Period]
    end: date

    @property
    def source(self) -> str:
        return ", ".join(sorted({p.source for p in self.periods}))

    @property
    def reported_at(self) -> date | None:
        dates = [p.reported_at for p in self.periods if p.reported_at]
        return max(dates) if dates else None

    def total(self, name: str) -> float | None:
        vals = [p.get(name) for p in self.periods]
        if any(v is None for v in vals):
            return None
        return float(sum(vals))  # type: ignore[arg-type]


def flow_basis(periods: list[Period], offset_years: int = 0) -> Basis | None:
    """TTM from 4 consecutive quarters, or the latest annual. ``offset_years`` looks back a year (for growth)."""
    qs = sorted([p for p in periods if p.period_type == "Q"], key=lambda p: p.period_end, reverse=True)
    ann = sorted([p for p in periods if p.period_type == "A"], key=lambda p: p.period_end, reverse=True)
    q_window = qs[offset_years * 4 : offset_years * 4 + 4]
    latest_annual = ann[offset_years] if len(ann) > offset_years else None
    if _consecutive_quarters(q_window) and (
        latest_annual is None or q_window[0].period_end >= latest_annual.period_end
    ):
        return Basis(f"TTM to {q_window[0].period_end.isoformat()}", q_window, q_window[0].period_end)
    if latest_annual is not None:
        return Basis(f"FY ending {latest_annual.period_end.isoformat()}", [latest_annual], latest_annual.period_end)
    return None


def latest_with(periods: list[Period], name: str, before: date | None = None) -> Period | None:
    cands = sorted(
        [p for p in periods if p.get(name) is not None and (before is None or p.period_end <= before)],
        key=lambda p: p.period_end,
        reverse=True,
    )
    return cands[0] if cands else None


def _ratio(
    value: float | None,
    formula: str,
    basis: str,
    source: str,
    reported_at: date | None,
    reason: str | None = None,
    unit: str = "",
) -> dict[str, Any]:
    return {
        "value": None if value is None else round(value, 4),
        "formula": formula,
        "period": basis,
        "source": source,
        "reported_at": reported_at.isoformat() if reported_at else None,
        "reason": reason if value is None else None,
        "unit": unit,
    }


def compute_ratios(
    all_periods: list[Period], price: float | None, price_source: str | None = None, price_as_of: str | None = None
) -> dict[str, Any]:
    periods = [p for p in all_periods if not p.validation_errors]
    rejected = len(all_periods) - len(periods)
    out: dict[str, Any] = {
        "price": price,
        "price_source": price_source,
        "price_as_of": price_as_of,
        "periods_used": len(periods),
        "periods_rejected": rejected,
        "ratios": {},
    }
    r = out["ratios"]
    if not periods:
        out["note"] = "No validated financial statements available"
        return out

    flow = flow_basis(periods)
    prev = flow_basis(periods, offset_years=1)
    eq_p = latest_with(periods, "total_equity")
    sh_p = latest_with(periods, "shares_outstanding")
    shares = sh_p.get("shares_outstanding") if sh_p else None
    equity = eq_p.get("total_equity") if eq_p else None
    no_price = "No current price available" if not price else None

    def bs(p: Period | None) -> tuple[str, str, date | None]:
        return (f"as of {p.period_end.isoformat()}", p.source, p.reported_at) if p else ("—", "—", None)

    if flow:
        eps, revenue, np_ = flow.total("eps"), flow.total("revenue"), flow.total("net_profit")
        ebitda, ebit = flow.total("ebitda"), flow.total("ebit")
        ocf, capex, dps = flow.total("operating_cash_flow"), flow.total("capex"), flow.total("dividend_per_share")
        src, rep, lbl = flow.source, flow.reported_at, flow.label

        if no_price:
            r["pe"] = _ratio(None, "Price / EPS", lbl, src, rep, no_price, "x")
        elif eps is None:
            r["pe"] = _ratio(None, "Price / EPS", lbl, src, rep, "EPS not reported", "x")
        elif eps <= 0:
            r["pe"] = _ratio(None, "Price / EPS", lbl, src, rep, "Not meaningful for zero or negative earnings", "x")
        else:
            r["pe"] = _ratio(price / eps, "Price / EPS", lbl, src, rep, unit="x")
        r["eps"] = _ratio(eps, "Sum of EPS", lbl, src, rep, None if eps is not None else "EPS not reported", "INR")
        r["revenue"] = _ratio(
            revenue, "Revenue", lbl, src, rep, None if revenue is not None else "Not reported", "INR cr"
        )
        r["net_profit"] = _ratio(
            np_, "Net profit", lbl, src, rep, None if np_ is not None else "Not reported", "INR cr"
        )
        r["ebitda"] = _ratio(ebitda, "EBITDA", lbl, src, rep, None if ebitda is not None else "Not reported", "INR cr")
        margin_reason = "Revenue or profit not reported" if not revenue else None
        r["ebitda_margin"] = _ratio(
            ebitda / revenue * 100 if revenue and ebitda is not None else None,
            "EBITDA / Revenue × 100",
            lbl,
            src,
            rep,
            margin_reason,
            "%",
        )
        r["net_margin"] = _ratio(
            np_ / revenue * 100 if revenue and np_ is not None else None,
            "Net profit / Revenue × 100",
            lbl,
            src,
            rep,
            margin_reason,
            "%",
        )
        fcf = ocf - capex if ocf is not None and capex is not None else None
        r["free_cash_flow"] = _ratio(
            fcf,
            "Operating cash flow − Capex",
            lbl,
            src,
            rep,
            None if fcf is not None else "Cash flow or capex not reported",
            "INR cr",
        )
        eq_q = ocf / np_ if ocf is not None and np_ and np_ > 0 else None
        r["earnings_quality"] = _ratio(
            eq_q,
            "Operating cash flow / Net profit",
            lbl,
            src,
            rep,
            None if eq_q is not None else "Needs positive profit and cash flow data",
            "x",
        )
        if no_price or dps is None:
            r["dividend_yield"] = _ratio(
                None, "DPS / Price × 100", lbl, src, rep, no_price or "Dividend not reported", "%"
            )
        else:
            r["dividend_yield"] = _ratio(dps / price * 100, "DPS / Price × 100", lbl, src, rep, unit="%")

        if equity and equity > 0 and np_ is not None:
            prior_eq = latest_with(periods, "total_equity", before=flow.end - timedelta(days=300))
            avg_eq = (equity + prior_eq.get("total_equity")) / 2 if prior_eq else equity  # type: ignore[operator]
            basis_note = "average of opening and closing equity" if prior_eq else "closing equity"
            r["roe"] = _ratio(np_ / avg_eq * 100, f"Net profit / {basis_note} × 100", lbl, src, rep, unit="%")
        else:
            r["roe"] = _ratio(
                None, "Net profit / Equity × 100", lbl, src, rep, "Needs positive equity and net profit", "%"
            )

        ta_p = latest_with(periods, "total_assets")
        cl = ta_p.get("current_liabilities") if ta_p else None
        if ebit is not None and ta_p and cl is not None and (ta_p.get("total_assets") or 0) - cl > 0:
            ce = ta_p.get("total_assets") - cl  # type: ignore[operator]
            r["roce"] = _ratio(
                ebit / ce * 100, "EBIT / (Total assets − Current liabilities) × 100", lbl, src, rep, unit="%"
            )
        else:
            r["roce"] = _ratio(
                None,
                "EBIT / Capital employed × 100",
                lbl,
                src,
                rep,
                "Needs EBIT, total assets and current liabilities",
                "%",
            )

        if prev:
            for key, fname in (("revenue_growth", "revenue"), ("profit_growth", "net_profit")):
                cur, old = flow.total(fname), prev.total(fname)
                if cur is not None and old and old > 0:
                    r[key] = _ratio(
                        (cur / old - 1) * 100, f"{fname} YoY change", f"{lbl} vs {prev.label}", src, rep, unit="%"
                    )
                else:
                    r[key] = _ratio(
                        None, f"{fname} YoY change", lbl, src, rep, "Needs a positive prior-year figure", "%"
                    )
        else:
            for key in ("revenue_growth", "profit_growth"):
                r[key] = _ratio(None, "YoY change", lbl, src, rep, "Needs a prior-year period", "%")

    lbl, src, rep = bs(eq_p)
    debt_p = latest_with(periods, "total_debt")
    if equity and equity > 0 and debt_p:
        r["debt_to_equity"] = _ratio(
            debt_p.get("total_debt") / equity,
            "Total debt / Equity",
            lbl,
            src,
            rep,  # type: ignore[operator]
            unit="x",
        )
    else:
        r["debt_to_equity"] = _ratio(None, "Total debt / Equity", lbl, src, rep, "Needs debt and positive equity", "x")
    if price and equity and equity > 0 and shares:
        r["pb"] = _ratio(price / (equity / shares), "Price / (Equity / Shares)", lbl, src, rep, unit="x")
        r["book_value_per_share"] = _ratio(equity / shares, "Equity / Shares", lbl, src, rep, unit="INR")
    else:
        reason = no_price or "Needs positive equity and shares outstanding"
        r["pb"] = _ratio(None, "Price / Book value per share", lbl, src, rep, reason, "x")
    sl, ss, sr = bs(sh_p)
    if price and shares:
        r["market_cap"] = _ratio(
            price * shares, "Price × Shares outstanding", f"price now, shares {sl}", ss, sr, unit="INR cr"
        )
    else:
        r["market_cap"] = _ratio(
            None, "Price × Shares", sl, ss, sr, no_price or "Shares outstanding not reported", "INR cr"
        )
    return out


def screener_values(ratios: dict[str, Any]) -> dict[str, float | None]:
    """Flatten ratios to plain numbers for screening."""
    return {k: v.get("value") for k, v in (ratios.get("ratios") or {}).items()}
