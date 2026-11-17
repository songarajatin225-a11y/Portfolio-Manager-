# MarketIntel — Technical Architecture

This document answers the prompt's "first task": architecture, API requirements, market data providers and their authentication, the database schema, the roadmap, external services and costs, and deployment requirements.

> **Status legend used throughout the docs:** ✅ implemented and tested locally · 🟡 implemented, needs live credentials before it can be checked end to end · ⏳ planned.

---

## 1. Starting point

The GitHub repository `Portfolio-Manager-` held only a one-line `README.md` and one commit, so this is a greenfield build. There was no existing code, schema or deployment to keep compatible with.

## 2. System architecture

```
                         ┌───────────────────────────────────────────────┐
 Browser ──HTTPS──▶ Next.js (web)  /api/* rewrite ──▶ FastAPI (api)       │
    │                 SSR + UI        same-origin cookies   REST /api/v1   │
    │                                                       WS   /ws/stream│
    └──────WSS (short-lived ticket)───────────────────────────▲           │
                                                              │           │
                       Redis  ◀── pub/sub: ticks, notifications, commands │
                         ▲                                                │
                         │                                                │
  Broker APIs ◀──REST/WSS── Worker (feed manager, candle builder,         │
  (Upstox, Angel One,       metrics refresh, alert engine, news ingest,   │
   custom providers)        ML scheduler)                                 │
                         │                                                │
                         ▼                                                │
                  PostgreSQL (+ TimescaleDB if available) ◀───────────────┘
                         ▲
  Claude API ◀── AI service (tool use over the platform's own data only)
```

### Processes

| Process | Tech | Responsibility |
|---|---|---|
| `web` | Next.js (App Router), React, TypeScript, Tailwind, shadcn-style components, TradingView Lightweight Charts, Recharts | UI. Proxies `/api/*` to the backend so auth cookies are first-party. Never holds API keys. |
| `api` | FastAPI, Pydantic v2, SQLAlchemy 2 (async), asyncpg | REST API, WebSocket fan-out, auth, RBAC, audit log, admin settings, AI endpoints, analytics. |
| `worker` | Python asyncio | Holds the single upstream broker connection. Normalises ticks, builds 1-minute candles, runs feed health checks, refreshes screener metrics, evaluates alerts, ingests news, runs scheduled ML retraining. |
| PostgreSQL | 16 (+ TimescaleDB extension when available) | System of record. Candles become a hypertable when TimescaleDB is installed; otherwise a plain table with a composite primary key. |
| Redis | 7 | Latest-quote cache, tick pub/sub, notification pub/sub, worker command channel, rate limiting. |

For small deployments `RUN_WORKER_IN_API=true` runs the worker loops inside the API process (one service instead of two). Without Redis an in-memory bus is used, but only in that embedded mode.

### Code layout

```
backend/app/
  core/        config, security (JWT/argon2/CSRF), crypto (Fernet), logging, errors, rate limiting
  db/          engine/session, ORM models
  api/v1/      routers (auth, users, market, instruments, stocks, watchlists, portfolios, journal,
               backtests, screener, ml, ai, news, alerts, risk, admin, system)
  marketdata/  provider interface, Upstox, Angel One, custom REST, custom WS, simulated provider,
               feed manager, candle builder, calendar, data-status labelling
  analytics/   indicators, fundamentals ratios, portfolio maths (FIFO/XIRR/CAGR), risk (VaR etc.),
               trading-journal statistics, backtesting engine, rule DSL (shared by screener/backtests/alerts)
  ml/          features, labels, models, walk-forward training, registry, drift (PSI)
  ai/          Claude client wrapper, tools over platform data, research reports, journal coach
  news/        RSS ingestion, de-duplication, entity tagging, lexicon sentiment
  alerts/      alert engine and notification channels (in-app, email, Telegram)
  workers/     worker entrypoint and schedulers
frontend/src/  app routes (16 modules), components, API client, WebSocket hook
```

### Design rules that carry through the codebase

1. **One provider interface.** Every market data source implements `MarketDataProvider` (`authenticate`, `get_quotes`, `get_candles`, `get_instruments`, `get_market_status`, `stream`). Switching providers is an admin setting, never a code change.
2. **Every data point carries a label.** Quotes and candles have `data_status ∈ {LIVE, DELAYED, HISTORICAL, SIMULATED}`, `source` and `as_of` (and `is_stale` for quotes). The UI renders those labels next to every number.
3. **Simulated data is fenced off.** The simulated provider only runs when `ALLOW_SIMULATED_FEED=true`. Every row it writes has `is_simulated=true`; every read filters on the current data mode, so simulated and real data can never be mixed. The UI shows a permanent red banner while simulation is on.
4. **No look-ahead.** Indicators use only past bars. Backtests act on the next bar's open after a signal. ML labels are forward returns, features are trailing, and splits are chronological with an embargo gap.
5. **AI never invents data.** Claude only sees data returned by the platform's tools. Each tool result includes its source and timestamp, and the answer must cite them. Missing data is reported as missing.
6. **Secrets stay on the backend.** Credentials are encrypted at rest (Fernet), masked in every response, and never sent to the browser.

## 3. API requirements

### 3.1 Platform REST API (served by `api`, prefix `/api/v1`)

| Area | Endpoints (summary) |
|---|---|
| Auth | `POST /auth/register` (bootstrap/first user or when public registration is enabled), `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/ws-ticket` |
| Users (admin) | `GET/POST /users`, `PATCH /users/{id}` (role, active) |
| Market | `GET /market/overview`, `GET /market/breadth`, `GET /market/movers`, `GET /market/sectors`, `GET /market/heatmap`, `GET /market/status`, `GET /market/quotes?symbols=` |
| Instruments | `GET /instruments/search`, `GET /instruments/{symbol}`, `POST /instruments/sync` (admin), `POST /instruments/import-constituents` (admin, NSE index CSV) |
| Stocks | `GET /stocks/{symbol}/candles`, `/technicals`, `/fundamentals`, `/institutional`, `/news`, `/prediction` |
| Fundamentals | `POST /fundamentals/import` (CSV), `POST /fundamentals/{symbol}` (manual), `POST /fundamentals/{symbol}/sync` (provider) |
| Watchlists | CRUD + items |
| Portfolios | CRUD, transactions CRUD, `GET /portfolios/{id}/summary`, `/performance`, `/risk`, `/rebalance`, CSV import/export |
| Journal | trades CRUD, `GET /journal/stats`, `POST /journal/coach` (AI) |
| Backtests | `POST /backtests` (run), `GET /backtests`, `GET /backtests/{id}` |
| Screener | `POST /screener/run`, saved screens CRUD, `GET /screener/fields` |
| ML | `GET /ml/models`, `POST /ml/train` (admin), `POST /ml/models/{id}/promote` (admin), `GET /ml/predictions`, `GET /ml/accuracy`, `GET /ml/drift` |
| AI | `POST /ai/chat`, `POST /ai/reports`, `GET /ai/reports`, `GET /ai/reports/{id}`, `POST /ai/market-summary` |
| News | `GET /news`, `POST /news/ingest` (admin), feed sources CRUD (admin) |
| Alerts | CRUD alerts, `GET /notifications`, `POST /notifications/{id}/read` |
| Risk | `GET /risk/portfolio/{id}`, `POST /risk/position-size`, `POST /risk/scenario` |
| Admin | provider configs CRUD, `POST /admin/providers/{id}/test`, `POST /admin/providers/{id}/activate`, OAuth callback for Upstox, app settings, audit log, feed health |
| System | `GET /health` (liveness), `GET /health/ready` (DB + Redis), `GET /system/status` (per-service status) |
| Streaming | `WS /ws/stream?ticket=…` — ticks, feed status, notifications |

OpenAPI docs are served at `/api/docs` (disabled in production unless `EXPOSE_API_DOCS=true`).

### 3.2 External APIs consumed

| Service | Purpose | Auth | Status |
|---|---|---|---|
| Upstox API v2/v3 | Quotes, historical candles, instrument master, market status & holidays, fundamentals, WebSocket V3 feed (protobuf) | OAuth 2.0 authorisation code → access token (expires daily ~03:30 IST) | 🟡 needs API key/secret + daily login |
| Angel One SmartAPI | Quotes, historical candles, scrip master, WebSocket 2.0 (binary) | API key + client code + PIN + TOTP → JWT (+ refresh token, feed token) | 🟡 needs API key, client code, PIN, TOTP secret |
| Custom REST provider | Any JSON REST quote/candle API, mapped by configurable JSON paths | API key header or bearer token | ✅ (tested against a local mock) |
| Custom WebSocket provider | Any JSON WebSocket tick stream, mapped by configurable paths | Token in URL/header | ✅ (tested against a local mock) |
| Anthropic Claude API | Research assistant, reports, market summaries, journal coach, optional news sentiment | `ANTHROPIC_API_KEY` (backend only) | 🟡 needs API key |
| RSS/Atom feeds | News and corporate announcements from publisher/exchange feeds the admin configures | none | ✅ |
| SMTP / Telegram Bot API | Optional alert channels | SMTP credentials / bot token | 🟡 optional |

## 4. Market data providers and authentication

### 4.1 Upstox (recommended primary)

Verified against the official `upstox-python-sdk` 2.30.0 package from PyPI (the docs site is not reachable from the build sandbox):

- **Login:** `GET https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=…&redirect_uri=…` → user logs in → redirect with `code` → `POST /v2/login/authorization/token` (form: `code, client_id, client_secret, redirect_uri, grant_type=authorization_code`). The access token is valid until about 03:30 IST the next day; **there is no refresh token**, so a human must log in once per trading day. The admin panel's "Connect Upstox" button runs this flow, and the feed health panel flags an expired token.
- **Quotes:** `GET /v3/market-quote/quotes?instrument_key=NSE_EQ|INE002A01018,…` (LTP, OHLC, `prev_close_price`, volume, `average_price` = VWAP, depth, OI, circuit limits, 52-week range).
- **Candles:** `GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}` and `/v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}`. Rows are `[timestamp, open, high, low, close, volume, oi]`.
- **Market status / holidays:** `GET /v2/market/status/{exchange}`, `GET /v2/market/holidays`.
- **Instrument master:** `https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz`.
- **Fundamentals:** `GET /v2/fundamentals/{isin}/income-statement|balance-sheet|cash-flow|share-holdings|key-ratios|corporate-actions|profile`. The SDK types these payloads loosely, so raw payloads are stored and fields are mapped through an editable alias table.
- **WebSocket:** `wss://api.upstox.com/v3/feed/market-data-feed` with `Authorization: Bearer …` (or the URL from `GET /v3/feed/market-data-feed/authorize`). Subscribe with a **binary** frame containing JSON `{"guid", "method": "sub"|"unsub"|"change_mode", "data": {"mode": "ltpc"|"full"|"option_greeks"|"full_d30", "instrumentKeys": [...]}}`. Responses are protobuf `FeedResponse` messages; field numbers were decoded from the SDK's compiled descriptor and are implemented by a dependency-free decoder in `marketdata/upstox_proto.py`.

### 4.2 Angel One SmartAPI

Verified against the official `smartapi-python` 1.5.5 package:

- **Login:** `POST https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword` with `{clientcode, password (PIN), totp}` and headers `X-PrivateKey` (API key), `X-UserType: USER`, `X-SourceID: WEB`, `X-ClientLocalIP`, `X-ClientPublicIP`, `X-MACAddress`. Returns `jwtToken`, `refreshToken` and `feedToken`. The TOTP is generated server-side from the stored TOTP secret, so daily re-login is automatic.
- **Refresh:** `POST /rest/auth/angelbroking/jwt/v1/generateTokens`.
- **Quotes:** `POST /rest/secure/angelbroking/market/v1/quote` with `{"mode": "FULL", "exchangeTokens": {"NSE": ["2885"]}}`.
- **Candles:** `POST /rest/secure/angelbroking/historical/v1/getCandleData` (`ONE_MINUTE` … `ONE_DAY`).
- **Scrip master:** `https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json`.
- **WebSocket 2.0:** `wss://smartapisocket.angelone.in/smart-stream` with `Authorization`, `x-api-key`, `x-client-code` and `x-feed-token` headers; text heartbeat `ping`. Little-endian binary packets with LTP / Quote / SnapQuote layouts; prices are in paise.

### 4.3 Exchange data licensing — important

Broker APIs give the **account holder** market data for their own use. Showing that live data to other people is redistribution, which needs a data licence from the exchange (NSE/BSE data vendor agreements). For that reason:

- Public registration is **off** by default; the first user becomes admin and adds other users.
- The deployment should stay private (you and your team). Opening it to the public with live NSE/BSE prices needs a licensed data vendor feed. The custom REST/WebSocket providers are there so a licensed feed can be plugged in later.
- No website scraping is done anywhere (no NSE/BSE site scraping, no Yahoo Finance). Historical data and index constituents can also be imported from CSV files you obtain legitimately.

## 5. Database schema

All tables have `id` primary keys (bigint identity or UUID for users) plus `created_at`/`updated_at` where relevant. The full definitions are in `backend/app/db/models/` and the Alembic migrations.

| Table | Key columns | Notes |
|---|---|---|
| `users` | id (uuid), email (unique), password_hash (argon2id), full_name, role (`admin`/`analyst`/`viewer`), is_active, last_login_at | |
| `refresh_tokens` | jti, user_id, family_id, token_hash, expires_at, revoked_at, replaced_by | Rotation with reuse detection: replaying an old token revokes the whole family. |
| `audit_logs` | user_id, action, entity_type, entity_id, ip, user_agent, details (jsonb), created_at | Auth events, settings and credential changes, destructive actions. |
| `app_settings` | key (pk), value (jsonb), updated_by | Non-secret settings: AI model, refresh intervals, timezone, notification settings, dashboard index map, news feeds. |
| `provider_configs` | name, provider_type, rest_url, ws_url, exchange, interval, feed_type, options (jsonb), encrypted_credentials, is_active, last_test_* , token_expires_at | Only one active provider. Credentials are Fernet-encrypted. |
| `instruments` | symbol, exchange, segment, name, isin, instrument_type, lot_size, tick_size, expiry, strike, option_type, sector, industry, is_index | Unique (exchange, segment, symbol, expiry, strike, option_type). |
| `instrument_provider_keys` | instrument_id, provider_type, provider_key, provider_token | Symbol mapping per provider. |
| `index_constituents` | index_code, instrument_id, start_date, end_date | Point-in-time membership, used to reduce survivorship bias. |
| `candles` | instrument_id, interval, ts, open, high, low, close, volume, oi, source, is_simulated | PK (instrument_id, interval, ts, is_simulated). Hypertable on `ts` when TimescaleDB is present. |
| `instrument_metrics` | instrument_id, as_of, last_close, change_pct, volume, avg_volume_20, rsi_14, macd, macd_signal, ema_20/50/200, sma_50/200, atr_14, adx_14, ret_1w/1m/3m/6m/1y, rel_strength, high_52w, low_52w, volatility_20, flags (jsonb), is_simulated | Snapshot that feeds the screener, heatmap and breadth. |
| `fundamentals` | instrument_id, period_type (Q/A), period_end, statement_type (consolidated/standalone), revenue, ebitda, ebit, net_profit, eps, total_equity, total_debt, total_assets, current_liabilities, cash, operating_cash_flow, capex, shares_outstanding, dividend_per_share, source, source_ref, reported_at, raw (jsonb), validation_errors (jsonb) | Ratios are computed from these validated inputs only. |
| `shareholding` | instrument_id, period_end, promoter_pct, fii_pct, dii_pct, public_pct, others_pct, source, reported_at | |
| `institutional_flows` | trade_date, category (FII/DII), segment, buy_value, sell_value, net_value, source | |
| `bulk_block_deals` | trade_date, instrument_id, deal_type, client_name, side, quantity, price, source | |
| `watchlists` / `watchlist_items` | user_id, name / watchlist_id, instrument_id, position, notes | |
| `portfolios` | user_id, name, description, benchmark_symbol, target_allocations (jsonb) | |
| `transactions` | portfolio_id, instrument_id, side (BUY/SELL/DIVIDEND/SPLIT), quantity, price, trade_date, charges, category, notes | FIFO lot matching for realised P&L. |
| `journal_trades` | user_id, instrument_id, direction, entry/exit date & price, quantity, stop_loss, target, strategy, brokerage, taxes, entry_reason, exit_reason, tags, emotions, status | |
| `saved_screens` | user_id, name, definition (jsonb) | |
| `backtests` | user_id, name, config (jsonb), status, result (jsonb), error | |
| `ml_models` | name, task, algorithm, version, status (`candidate`/`active`/`retired`), params, features, train/val/test ranges, metrics, calibration, reference_stats, artifact (bytea), data_hash | |
| `ml_predictions` | model_id, instrument_id, as_of, horizon_days, prob_up/down/neutral, predicted_vol, trend_class, confidence, realized_label, realized_return, evaluated_at, is_simulated | |
| `news_sources` | name, url, kind (rss), category, is_active, last_fetched_at, last_error | |
| `news_articles` | source_id, url (unique), url_hash, title_hash, title, summary, published_at, sentiment_label, sentiment_score, sentiment_method, impact, category | De-duplicated on canonical URL and normalised title. |
| `news_article_instruments` | article_id, instrument_id, match_type | |
| `alerts` | user_id, alert_type, instrument_id, portfolio_id, params (jsonb), channels, is_active, trigger_once, cooldown_minutes, last_triggered_at | |
| `notifications` | user_id, alert_id, title, body, severity, data, read_at | |
| `ai_reports` | user_id, instrument_id, kind, model, content (jsonb), sources (jsonb), data_quality (jsonb), usage (jsonb) | |
| `ai_conversations` / `ai_messages` | user_id, title / conversation_id, role, content, sources | Chat history. |
| `market_holidays` | exchange, date, description | Fallback calendar when the provider cannot supply holidays. |

## 6. Development roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| 1 Foundation | Repo layout, FastAPI app, config, DB + migrations, auth (argon2, JWT cookies, refresh rotation, CSRF, RBAC), audit log, Next.js shell, dark responsive layout, login | API tests pass; UI builds; login works end to end |
| 2 Live data | Provider interface, Upstox, Angel One, custom REST/WS, simulated provider, feed manager (reconnect, backoff, token refresh, rate limits, stale detection, health), candle builder, instrument sync, WS fan-out, admin provider settings + Test Connection | Decoders tested with fixtures; reconnection tested against a local WS server; UI shows live (simulated in dev) ticks with labels |
| 3 Stock intelligence | Indicators, fundamentals ratios, stock explorer and research page, screener + saved screens, heatmap, breadth/movers/sectors | Indicator values checked against reference implementations |
| 4 AI/ML | Claude tool-use assistant, structured research reports, market summary, journal coach; news ingestion + sentiment; ML features, labels, walk-forward training, calibration, registry, drift, prediction dashboard | Leakage tests pass; AI degrades gracefully without a key |
| 5 Portfolio | Portfolio ledger + analytics, CSV import/export, rebalancing, trading journal, risk engine, backtester, alerts + notifications | Finance maths unit-tested against hand-computed cases |
| 6 Deployment | Dockerfiles, Compose, GitHub Actions CI/CD, Render blueprint, docs | CI green; deployed URL verified (needs hosting credentials) |

## 7. External services and estimated monthly cost

Prices change; check each vendor before committing. Figures are rough estimates as of late 2026.

| Service | Needed for | Estimated cost |
|---|---|---|
| Upstox API | Live and historical data, fundamentals | Requires an Upstox account. API access has historically been free or a small monthly fee — check Upstox's current pricing. |
| Angel One SmartAPI | Alternative live/historical data | Free with an Angel One account (at last check). |
| Anthropic Claude API | AI assistant and reports | Usage-based. Default model `claude-opus-5-5` at $4 / $20 per million input/output tokens. A research report uses roughly 10–20k input and 2–4k output tokens (about $0.10–$0.16); a chat question with tool calls is usually $0.03–$0.20. Switching the model to `claude-sonnet-5-5` in settings halves this. |
| Hosting — option A: Render | API + worker + web + Postgres + Redis | About $35–50/month on paid starter plans (3 × $7 services, Postgres ~$6–20, Key Value ~$10). Free tiers sleep and expire, so they suit demos only. TimescaleDB is not available on Render Postgres; the plain-table fallback is used. |
| Hosting — option B: a VPS with Docker Compose | Everything, including TimescaleDB | $5–12/month (e.g. Hetzner, DigitalOcean); you run the server and backups. |
| Frontend on Vercel (optional) | Web only | Hobby plan is free for non-commercial use; Pro is $20/month. |
| Timescale Cloud (optional) | Managed TimescaleDB | From about $30/month. |
| News | RSS feeds | Free; respect each publisher's terms. Paid news APIs can be added later. |
| Email / Telegram alerts | Optional | Free tiers (Telegram bot; SMTP from your provider). |

## 8. Deployment requirements

- **Runtime:** Python 3.12 container for `api`/`worker`; Node 22 for `web` (Next.js standalone output).
- **Database:** PostgreSQL 15+; TimescaleDB optional. Migrations run with `alembic upgrade head` on deploy.
- **Redis 7** for production (required when `api` and `worker` are separate processes).
- **WebSockets:** the backend host must support long-lived WebSocket connections (Render, Fly.io, Railway and any VPS do; Vercel serverless functions do not, which is why the backend is not on Vercel).
- **TLS** everywhere; cookies are `Secure` in production.
- **Environment variables:** see `.env.example`. Required in production: `ENVIRONMENT=production`, `SECRET_KEY`, `SECRET_ENCRYPTION_KEY`, `DATABASE_URL`, `REDIS_URL`, `FRONTEND_ORIGIN`, `BACKEND_URL` (web), and `ANTHROPIC_API_KEY` for AI features. Broker credentials are entered in the admin panel, not in env files.
- **CI/CD:** GitHub Actions run lint, type checks, backend tests (against Postgres and Redis service containers), frontend tests and build, and Docker builds on every push and PR. On `main`, deploy hooks fire only after every check passes, followed by a smoke test against the deployed `/api/v1/health`.
- **Upstox redirect URI:** register `https://<your-web-domain>/api/v1/admin/providers/upstox/callback` in the Upstox developer console.
