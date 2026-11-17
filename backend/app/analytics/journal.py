"""Trading-journal statistics and rule-based behaviour checks (pure functions)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np


@dataclass
class JTrade:
    id: int
    symbol: str
    direction: str  # LONG | SHORT
    status: str
    entry_at: datetime
    entry_price: float
    quantity: float
    exit_at: datetime | None = None
    exit_price: float | None = None
    stop_loss: float | None = None
    target: float | None = None
    strategy: str = ""
    costs: float = 0.0
    emotion: str = ""
    tags: tuple[str, ...] = ()

    @property
    def sign(self) -> int:
        return 1 if self.direction == "LONG" else -1

    @property
    def gross_pnl(self) -> float | None:
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) * self.quantity * self.sign

    @property
    def net_pnl(self) -> float | None:
        g = self.gross_pnl
        return None if g is None else g - self.costs

    @property
    def initial_risk(self) -> float | None:
        if self.stop_loss is None:
            return None
        r = abs(self.entry_price - self.stop_loss) * self.quantity
        return r if r > 0 else None

    @property
    def r_multiple(self) -> float | None:
        n, r = self.net_pnl, self.initial_risk
        return None if n is None or r is None else n / r

    @property
    def return_pct(self) -> float | None:
        if self.exit_price is None or not self.entry_price:
            return None
        return (self.exit_price / self.entry_price - 1) * 100 * self.sign

    @property
    def holding_days(self) -> float | None:
        if self.exit_at is None:
            return None
        return (self.exit_at - self.entry_at).total_seconds() / 86400

    @property
    def planned_rr(self) -> float | None:
        if self.stop_loss is None or self.target is None or self.entry_price == self.stop_loss:
            return None
        return abs(self.target - self.entry_price) / abs(self.entry_price - self.stop_loss)


def _block(trades: list[JTrade]) -> dict[str, Any]:
    closed = [t for t in trades if t.net_pnl is not None]
    pnl = np.array([t.net_pnl for t in closed], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    return {
        "trades": len(closed),
        "net_pnl": round(float(pnl.sum()), 2) if len(pnl) else 0.0,
        "win_rate": round(len(wins) / len(pnl), 4) if len(pnl) else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
        "payoff_ratio": round(float(wins.mean() / -losses.mean()), 3)
        if len(wins) and len(losses) and losses.mean() < 0
        else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "expectancy": round(float(pnl.mean()), 2) if len(pnl) else None,
        "avg_r": round(float(np.mean(rs)), 3) if rs else None,
    }


def _streaks(pnl: list[float]) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for x in pnl:
        if x > 0:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def statistics(trades: list[JTrade]) -> dict[str, Any]:
    closed = sorted([t for t in trades if t.net_pnl is not None], key=lambda t: t.exit_at or t.entry_at)
    out = _block(closed)
    pnl = [t.net_pnl for t in closed]
    equity = np.cumsum(pnl) if pnl else np.array([])
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:] if len(equity) else np.array([])
    dd = equity - peak if len(equity) else np.array([])
    w, l_ = _streaks(pnl)
    holds = [t.holding_days for t in closed if t.holding_days is not None]
    win_holds = [t.holding_days for t in closed if t.holding_days is not None and t.net_pnl > 0]
    loss_holds = [t.holding_days for t in closed if t.holding_days is not None and t.net_pnl <= 0]
    out.update(
        {
            "open_trades": sum(1 for t in trades if t.status == "OPEN"),
            "largest_win": round(max(pnl), 2) if pnl else None,
            "largest_loss": round(min(pnl), 2) if pnl else None,
            "max_drawdown": round(float(dd.min()), 2) if len(dd) else None,
            "max_consecutive_wins": w,
            "max_consecutive_losses": l_,
            "avg_holding_days": round(float(np.mean(holds)), 2) if holds else None,
            "avg_holding_days_winners": round(float(np.mean(win_holds)), 2) if win_holds else None,
            "avg_holding_days_losers": round(float(np.mean(loss_holds)), 2) if loss_holds else None,
            "total_costs": round(sum(t.costs for t in closed), 2),
            "equity_curve": [
                {
                    "date": (t.exit_at or t.entry_at).date().isoformat(),
                    "symbol": t.symbol,
                    "pnl": round(t.net_pnl, 2),
                    "cumulative": round(float(c), 2),
                }
                for t, c in zip(closed, equity, strict=True)
            ],
        }
    )
    by_strategy: dict[str, list[JTrade]] = defaultdict(list)
    by_month: dict[str, list[JTrade]] = defaultdict(list)
    by_emotion: dict[str, list[JTrade]] = defaultdict(list)
    by_direction: dict[str, list[JTrade]] = defaultdict(list)
    for t in closed:
        by_strategy[t.strategy or "Unspecified"].append(t)
        by_month[(t.exit_at or t.entry_at).strftime("%Y-%m")].append(t)
        by_emotion[t.emotion or "Unspecified"].append(t)
        by_direction[t.direction].append(t)
    out["by_strategy"] = sorted(
        ({"strategy": k, **_block(v)} for k, v in by_strategy.items()), key=lambda x: -x["net_pnl"]
    )
    out["by_month"] = [{"month": k, **_block(by_month[k])} for k in sorted(by_month)]
    out["by_emotion"] = sorted(({"emotion": k, **_block(v)} for k, v in by_emotion.items()), key=lambda x: -x["trades"])
    out["by_direction"] = [{"direction": k, **_block(v)} for k, v in sorted(by_direction.items())]
    return out


def behaviour_findings(trades: list[JTrade]) -> list[dict[str, Any]]:
    """Deterministic checks for common discipline problems. Each finding cites the trades behind it."""
    closed = sorted([t for t in trades if t.net_pnl is not None], key=lambda t: t.entry_at)
    findings: list[dict[str, Any]] = []
    if not closed:
        return findings

    def add(code: str, severity: str, title: str, detail: str, ids: list[int]) -> None:
        findings.append({"code": code, "severity": severity, "title": title, "detail": detail, "trade_ids": ids[:50]})

    no_stop = [t.id for t in closed if t.stop_loss is None]
    if no_stop:
        add(
            "no_stop_loss",
            "warning",
            "Trades without a stop-loss",
            f"{len(no_stop)} of {len(closed)} closed trades had no stop-loss recorded, so risk was undefined.",
            no_stop,
        )

    overshoot = [t for t in closed if t.r_multiple is not None and t.r_multiple < -1.2]
    if overshoot:
        add(
            "stop_not_respected",
            "critical",
            "Losses larger than the planned risk",
            f"{len(overshoot)} trades lost more than 1.2× their planned risk "
            f"(worst {min(t.r_multiple for t in overshoot):.1f}R): stops were moved, ignored or gapped through.",
            [t.id for t in overshoot],
        )

    win_h = [t.holding_days for t in closed if t.net_pnl > 0 and t.holding_days is not None]
    loss_h = [t.holding_days for t in closed if t.net_pnl <= 0 and t.holding_days is not None]
    if len(win_h) >= 3 and len(loss_h) >= 3 and np.mean(loss_h) > 1.5 * np.mean(win_h):
        add(
            "holding_losers",
            "warning",
            "Holding losers longer than winners",
            f"Losing trades were held {np.mean(loss_h):.1f} days on average vs {np.mean(win_h):.1f} days for winners "
            "(disposition effect).",
            [t.id for t in closed if t.net_pnl <= 0],
        )

    revenge = []
    for prev, nxt in zip(closed, closed[1:], strict=False):
        if prev.net_pnl < 0 and prev.exit_at and timedelta(0) <= nxt.entry_at - prev.exit_at <= timedelta(hours=1):
            if nxt.quantity * nxt.entry_price > 1.3 * prev.quantity * prev.entry_price:
                revenge.append(nxt.id)
    if revenge:
        add(
            "revenge_trading",
            "critical",
            "Possible revenge trades",
            f"{len(revenge)} trades were opened within an hour of a loss with a position at least 30% larger.",
            revenge,
        )

    per_day: dict[str, list[int]] = defaultdict(list)
    for t in closed:
        per_day[t.entry_at.date().isoformat()].append(t.id)
    counts = [len(v) for v in per_day.values()]
    if len(counts) >= 5:
        median = float(np.median(counts))
        busy = {d: v for d, v in per_day.items() if len(v) >= max(5, 3 * median)}
        if busy:
            day_pnl = {d: sum(t.net_pnl for t in closed if t.entry_at.date().isoformat() == d) for d in busy}
            add(
                "overtrading",
                "warning",
                "Overtrading days",
                f"{len(busy)} days had at least 3× your usual number of trades; net P&L on those days was "
                f"{sum(day_pnl.values()):,.0f}.",
                [i for v in busy.values() for i in v],
            )

    sizes = np.array([t.quantity * t.entry_price for t in closed])
    if len(sizes) >= 8 and sizes.mean() > 0 and sizes.std() / sizes.mean() > 1.0:
        add(
            "inconsistent_sizing",
            "info",
            "Inconsistent position sizes",
            f"Position value varies widely (coefficient of variation {sizes.std() / sizes.mean():.1f}). "
            "Sizing by risk keeps each loss comparable.",
            [],
        )

    poor_rr = [t.id for t in closed if t.planned_rr is not None and t.planned_rr < 1]
    if poor_rr:
        add(
            "poor_risk_reward",
            "info",
            "Planned reward smaller than risk",
            f"{len(poor_rr)} trades were planned with a reward/risk below 1:1.",
            poor_rr,
        )

    early = [
        t
        for t in closed
        if t.net_pnl > 0
        and t.target is not None
        and t.exit_price is not None
        and abs(t.exit_price - t.entry_price) < 0.5 * abs(t.target - t.entry_price)
    ]
    if len(early) >= 3:
        add(
            "cutting_winners",
            "info",
            "Winners closed well before target",
            f"{len(early)} winning trades were closed before reaching half of the planned target.",
            [t.id for t in early],
        )

    emo = defaultdict(list)
    for t in closed:
        if t.emotion:
            emo[t.emotion].append(t.net_pnl)
    for e, vals in emo.items():
        if len(vals) >= 3 and sum(vals) < 0 and sum(1 for v in vals if v <= 0) / len(vals) >= 0.65:
            add(
                f"emotion_{e.lower()}",
                "info",
                f"Trades tagged '{e}' mostly lose",
                f"{sum(1 for v in vals if v <= 0)} of {len(vals)} trades tagged '{e}' were losses "
                f"(net {sum(vals):,.0f}).",
                [t.id for t in closed if t.emotion == e],
            )
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(findings, key=lambda f: order[f["severity"]])
