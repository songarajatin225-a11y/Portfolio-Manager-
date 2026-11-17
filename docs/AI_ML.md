# AI & Machine Learning

## AI research assistant (Claude)

- **Model:** set in System Administration → Settings → AI provider (default `claude-opus-5-5`). The key is read only from the backend's `ANTHROPIC_API_KEY`. Without a key, AI endpoints return `503 ai_unavailable` and the UI explains why; everything else keeps working.
- **Grounding:** Claude answers through tools that read this platform's own data: market overview, quote, technical analysis, fundamentals, news, screener, ML prediction, unusual activity and sector strength. Every tool result carries its source, timestamp and data label, and the UI lists those sources under each answer. The system prompt forbids inventing prices, filings, announcements or analyst estimates, and requires a plain statement when data is SIMULATED or missing.
- **Request design:** the system prompt and tool list are identical on every request, so earlier turns stay valid and the prompt prefix is cached. Per-turn context such as the time and data mode goes in the user message. Within a turn, assistant output is echoed back unchanged during tool use; between turns, earlier messages are replayed as text.
- **Refusals:** on supported models the server-side `fallbacks: "default"` option is sent (beta `server-side-fallback-2026-07-01`), so a request declined by a safety classifier is retried on a suitable model. A final refusal is shown as such, never as an empty answer.
- **Research reports:** the platform gathers a data bundle deterministically, and Claude writes a 12-section report constrained to a JSON schema (structured outputs). A deterministic data-quality check (which datasets are present, whether data is simulated) is stored with the report, alongside the model's own confidence.
- **Market summary and news sentiment:** a daily summary from market statistics and headlines. Optional Claude headline classification (setting "Use Claude for news sentiment"; runs at low effort) replaces the lexicon labels, and the method is recorded on each article.
- **Cost controls:** 15 AI requests per minute per user, effort levels configurable per use, and prompt caching. Viewers can read reports but cannot generate them.

## ML pipeline

| Model | Target | Algorithms | Validation |
|---|---|---|---|
| Trend classifier | Forward return over *h* days (default 5) vs a neutral band of `0.5 × ATR% × √h`: bearish / neutral / bullish | Logistic regression, random forest, XGBoost, LightGBM (best by walk-forward macro-F1) | Chronological split, embargo, walk-forward, sigmoid calibration on validation, single test evaluation |
| Momentum classifier | Outperform the benchmark over the next 21 days | Same four | Same |
| Volatility forecaster | Annualised realised volatility over the next 21 days | HAR-style linear regression, LightGBM | Test MAE vs the naive "current volatility" forecast |
| Unusual activity | Volume / move / range / gap anomaly (unsupervised) | Isolation forest | Percentile vs training distribution; no accuracy claimed |
| Market regime | Benchmark 21-day return and volatility clusters | Gaussian mixture | Unsupervised; occupancy reported |

Leakage controls:

- Features use only data up to each date's close: trailing windows, shifted averages, and news published on or before that date.
- Labels are the only forward-looking values. Rows without a full label window are never trained on.
- An embargo of *h* trading days separates train, validation and test, and each walk-forward fold.
- Tests check that features don't change when future bars are added, and that the splits respect the embargo.

Registry and promotion:

- Each run creates a new versioned candidate, stored with its metrics, calibration curve, training/test periods, feature reference distributions and data fingerprint.
- A classifier is promoted only if it beats the majority-class baseline on its test period **and** matches or beats the active model on that same period by the configured margin. Otherwise it is rejected with the reason recorded.
- Manual promotion of a model that failed validation is refused.
- Models publish independently: if the trend classifier is rejected, volatility, anomaly and regime outputs still appear, and movement probabilities are simply not shown.

Monitoring:

- Predictions are scored once their horizon passes, giving the realised hit-rate history.
- Drift is measured with the Population Stability Index of recent features (and of the predicted-class mix) against the training data: below 0.1 stable, 0.1–0.25 moderate, above 0.25 significant.

On the simulated feed, prices are random walks, so the trend and momentum classifiers should be (and are) rejected. That is the pipeline working as intended.
