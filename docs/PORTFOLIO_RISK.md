# Portfolio, journal, risk, backtesting and alerts

This page states how each number in these modules is calculated, so results can be checked by hand.

## Portfolio Manager

| Measure | Method |
|---|---|
| Cost basis | FIFO lot matching. Buy charges are added to the lot cost; sell charges are deducted from proceeds. |
| Realised P&L | Sell proceeds − sell charges − FIFO cost of the lots sold. Dividends are tracked separately. |
| Unrealised P&L | Quantity × current price − remaining FIFO cost. |
| Current price | The latest cached quote with its data label (LIVE, DELAYED, HISTORICAL or SIMULATED). If there is none, the last stored daily close. Holdings with no price at all are valued at cost and listed in a warning. |
| XIRR | Money-weighted annual return: the rate at which the dated cash flows (buys negative, sells and dividends positive) plus today's value have zero net present value. Solved with Brent's method; not shown when undefined. |
| Time-weighted return | Daily returns `r_t = (V_t − F_t) / V_{t−1} − 1`, where `F_t` is that day's net external flow (buys in, sell proceeds and dividends out), chained over the period. Deposits and withdrawals do not distort it. |
| CAGR | Annualised time-weighted return. Shown only after a full year of history. |
| Volatility, max drawdown | From the daily time-weighted return series (volatility annualised with √252). |
| Benchmark, beta | The portfolio's benchmark symbol if it resolves in the current data mode, else the platform benchmark index. Both are rebased to 100 on the same start date, so there is never a second y-axis. |
| Concentration | Weights, top-5 weight, Herfindahl-Hirschman index (sum of squared weights) and effective holdings (1 / HHI). |
| Rebalancing | Trades that move each holding from its current weight to target (custom targets that sum to 100%, or equal weight), for holdings that drift beyond a threshold. Taxes, exit loads and costs are ignored, and the page says so. |

Transactions are validated as a whole ledger: a sell larger than the holding on that date is rejected, as is deleting or editing a buy that a later sell depends on. CSV import validates every row before saving anything, with a dry-run mode that only checks. Export uses the same columns, so a file exported from one portfolio imports cleanly into another.

## Trading Journal

- **Net P&L** = (exit − entry) × quantity × direction − brokerage − taxes.
- **R-multiple** = net P&L ÷ planned risk, where planned risk is |entry − stop| × quantity. Trades without a stop have no R.
- **Profit factor** = gross wins ÷ gross losses. **Expectancy** = mean net P&L per trade. **Payoff ratio** = average win ÷ |average loss|.
- **Drawdown** is measured on cumulative closed-trade P&L.
- Breakdowns are by strategy, month (exit date), emotion and direction.

**Discipline checks** are deterministic rules, each citing the trades behind it:

- trades with no stop-loss;
- losses beyond 1.2R (stop moved, ignored or gapped through);
- losers held more than 1.5× as long as winners;
- possible revenge trades: a position at least 30% larger opened within an hour of a loss;
- overtrading days: at least 3× the usual daily count;
- inconsistent position sizes;
- planned reward/risk below 1:1;
- winners closed before half the target;
- emotion tags that mostly lose.

**AI coach.** Claude receives the statistics, the rule findings and up to 60 recent closed trades (with their entry and exit notes). It returns a structured review: summary, strengths, weaknesses, behavioural patterns with evidence, strategy review, rules to follow, and a sample-size note. It is told to cite evidence, never to invent trades, and never to recommend securities. It needs at least 5 closed trades and a configured `ANTHROPIC_API_KEY`.

## Risk Analytics

- **Returns window:** daily close-to-close returns of today's holdings over the chosen look-back (6 months to 3 years).
- **Historical VaR:** the empirical (1 − confidence) quantile of portfolio returns. **Parametric VaR:** assumes normally distributed returns, `−(μ + σ·z)`. **Expected shortfall:** the mean loss beyond the historical VaR. 10-day figures scale 1-day values by √10, which assumes independent returns. At least 30 observations are required. The full methodology text is shown next to the table.
- **Correlation:** pairwise correlation of daily returns, needing at least 30 overlapping days. Shown as a blue (move together), grey (unrelated) and red (move oppositely) matrix, with values printed in each cell.
- **Beta:** cov(r, r_benchmark) / var(r_benchmark), per holding and for the portfolio.
- **Diversification ratio:** weighted average holding volatility ÷ portfolio volatility.
- **Scenarios:** benchmark falls of 5%, 10%, 20% and 30% applied through each holding's beta (1.0 where unknown); volatility shocks of 1.5×, 2× and 3× applied to 1-day 99% parametric VaR. These are linear estimates, and the page says so.
- **Limits flagged:** a single position above 20%, a sector above 40%, average correlation above 0.7.
- **Position size calculator:** fixed-fractional sizing. Quantity = (capital × risk%) ÷ |entry − stop|, rounded down to the lot size and optionally capped by a maximum position %. Reward/risk is shown when a target is given.

## Strategy Backtesting

Built-in strategies:

- moving-average crossover;
- RSI mean reversion;
- MACD signal cross;
- Donchian breakout (optional volume confirmation);
- cross-sectional momentum (monthly rebalance into the top N by trailing return, skipping recent bars);
- custom rules.

Custom rules combine conditions with AND/OR. Each condition compares a series (`close`, `sma_N`, `ema_N`, `rsi_N`, `macd`, `macd_signal`, `macd_hist`, `bb_upper_N`, `bb_lower_N`, `atr_N`, `adx_N`, `supertrend_dir`, `highest_N`, `lowest_N`, `ret_N`, `vol_avg_N`) with a number or another series, using `>`, `<`, `>=`, `<=`, `crosses_above` or `crosses_below`.

Execution and bias controls:

- Signals use data up to each bar's close and fill at the **next bar's open**. A test asserts that adding future bars never changes past signals.
- Indicators warm up on about 420 calendar days of history before the test start.
- Stops and targets are checked against each bar's low and high. When both are touched in the same bar, the **stop fills first** (the conservative assumption). A gap through the stop fills at the open. Trailing stops use the highest high seen before the current bar.
- **Survivorship:** an index universe allows entries only into stocks that were members on that date, using point-in-time constituent history (imported under System Administration → Data). If the history starts after the test start, the earliest known members are used for the earlier part and a warning says so. Hand-picked symbol lists carry a selection-bias warning.
- Positions in instruments whose data ends early (possible delisting) are closed at the last close, never carried forward.
- Costs on every fill: brokerage (percentage with an optional per-order cap, default 0.03% capped at ₹20), statutory charges (default 0.11% per order, approximating STT, exchange fees, GST and stamp duty) and slippage (default 5 bps).
- Long-only, whole shares, no leverage. Taxes on gains are not modelled.

Reported metrics:

- total return and CAGR;
- annualised volatility;
- Sharpe and Sortino ratios, against a configurable risk-free rate (default 6.5%);
- maximum drawdown and Calmar ratio;
- trade count, win rate, profit factor, average trade, average holding period;
- exposure and total costs;
- benchmark buy-and-hold return and CAGR over the same window.

Results show an equity curve against the benchmark (same rupee scale), a drawdown chart, monthly returns and the full trade list. Fewer than 30 trades triggers a statistical-reliability warning. Runs on simulated data are labelled as such.

## Alerts and notifications

| Type | Evaluated on |
|---|---|
| Price above / below, stop-loss | Live (or delayed) cached quote |
| Day change beyond ±% | Quote vs previous close |
| Volume spike | Today's cumulative volume vs 20-day average (re-arms each session) |
| RSI above / below, MACD crossover | Daily-bar metrics snapshot |
| 52-week breakout / breakdown | Quote vs 52-week high / low |
| Portfolio drawdown | Time-weighted daily valuation (checked every 15 minutes) |
| News / announcement | Newly ingested articles tagged to the company, with optional keywords and a high-impact filter |
| AI unusual activity | ML anomaly percentile |

- **Edge-triggered:** an alert fires when its condition becomes true and re-arms only after the condition is false again, so a price sitting above a level does not notify on every cycle. MACD crossovers record their initial state silently and fire only on an actual cross.
- **Repeat limits:** a cooldown (default 60 minutes) and an optional "trigger once" setting.
- **Schedule:** the worker evaluates active alerts every `alert_eval_seconds` (default 30). Users can also run "Check now".
- **What each notification stores:** the value, source, timestamp and data label that triggered it, plus the delivery result for each channel.
- **Channels:**
  - **In-app:** stored, then pushed to the browser over the authenticated WebSocket. The bell in the top bar shows unread notifications.
  - **Email:** SMTP settings and an encrypted password, both under System Administration.
  - **Telegram:** bot token (encrypted) and chat id.
  - Admins can send a test message through either channel. Credentials stay on the backend and never appear in responses or errors.
