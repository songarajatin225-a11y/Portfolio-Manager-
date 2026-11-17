"""Portfolio, risk, journal, backtest and alert APIs end to end on the simulated provider."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
from httpx import AsyncClient

from app.ai import tools as ai_tools
from app.ai.coach import COACH_SCHEMA
from app.core.bus import CH_NOTIFICATIONS, K_QUOTE, MemoryBus
from app.db.models import Instrument, NewsArticle, NewsArticleInstrument
from app.db.session import get_sessionmaker
from app.services import notify
from app.services.alerts import evaluate_alerts
from tests.test_ai import msg, text
from tests.test_market_api import setup_simulated

API = "/api/v1"


async def _inst(symbol: str) -> Instrument:
    from sqlalchemy import select

    async with get_sessionmaker()() as db:
        return await db.scalar(select(Instrument).where(Instrument.symbol == symbol, Instrument.is_simulated.is_(True)))


async def _quote(bus: MemoryBus, inst: Instrument, ltp: float, **extra: float) -> None:
    q = {
        "provider_key": f"SIM:{inst.symbol}",
        "ltp": ltp,
        "received_at": datetime.now(UTC).isoformat(),
        "source": "simulated",
        "instrument_id": inst.id,
        "symbol": inst.symbol,
        "is_simulated": True,
        "prev_close": extra.get("prev_close", ltp),
        "volume": extra.get("volume", 1000.0),
    }
    await bus.set_json(f"{K_QUOTE}{inst.id}", q)


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


async def test_portfolio_lifecycle_risk_and_csv(admin_client: AsyncClient, bus: MemoryBus) -> None:
    await setup_simulated(admin_client)
    pid = (await admin_client.post(f"{API}/portfolios", json={"name": "Core"})).json()["id"]
    assert (await admin_client.post(f"{API}/portfolios", json={"name": "Core"})).status_code == 409

    async def add(**body) -> httpx.Response:  # noqa: ANN003
        return await admin_client.post(f"{API}/portfolios/{pid}/transactions", json=body)

    assert (
        await add(symbol="SIMBANKA", side="BUY", quantity=10, price=1000, charges=20, trade_date=_days_ago(300))
    ).status_code == 201
    assert (
        await add(symbol="SIMBANKB", side="BUY", quantity=40, price=200, trade_date=_days_ago(250))
    ).status_code == 201
    over = await add(symbol="SIMBANKA", side="SELL", quantity=20, price=1100, trade_date=_days_ago(10))
    assert over.status_code == 422 and over.json()["error"]["code"] == "invalid_ledger"
    future = await add(symbol="SIMBANKA", side="BUY", quantity=1, price=1, trade_date=_days_ago(-5))
    assert future.status_code == 422
    idx = await add(symbol="SIMNIFTY50", side="BUY", quantity=1, price=1, trade_date=_days_ago(5))
    assert idx.status_code == 422
    sell = await add(symbol="SIMBANKA", side="SELL", quantity=4, price=1100, charges=4, trade_date=_days_ago(20))
    assert sell.status_code == 201

    perf = (await admin_client.get(f"{API}/portfolios/{pid}/performance")).json()
    assert perf["available"] and perf["data_status"] == "SIMULATED" and len(perf["series"]) > 150
    assert perf["max_drawdown_pct"] <= 0 and "time-weighted" in perf["methodology"]

    a = await _inst("SIMBANKA")
    await _quote(bus, a, 1234.5)
    s = (await admin_client.get(f"{API}/portfolios/{pid}")).json()
    by = {h["symbol"]: h for h in s["holdings"]}
    assert by["SIMBANKA"]["quantity"] == 6 and by["SIMBANKA"]["price"] == 1234.5
    assert by["SIMBANKA"]["price_status"] == "SIMULATED"
    assert by["SIMBANKB"]["price_status"] == "SIMULATED"  # last stored daily close
    # realised = 4 x 1100 - 4 - 4 x 1002 (unit cost incl. charges)
    assert abs(s["realized_pnl"] - (4400 - 4 - 4008)) < 1e-6
    assert s["xirr_pct"] is not None and s["data_mode"] == "simulated"
    assert abs(sum(h["weight"] for h in s["holdings"]) - 1) < 1e-9

    risk = (await admin_client.get(f"{API}/portfolios/{pid}/risk")).json()
    assert risk["available"] and risk["var"]["available"]
    assert {r["confidence"] for r in risk["var"]["table"]} == {0.95, 0.99}
    assert risk["correlation"]["labels"] and risk["scenarios"]["market_decline"]
    assert "not a forecast" in risk["disclaimer"]

    bad = await admin_client.patch(f"{API}/portfolios/{pid}", json={"target_allocations": {"SIMBANKA": 0.9}})
    assert bad.status_code == 422
    ok = await admin_client.patch(
        f"{API}/portfolios/{pid}", json={"target_allocations": {"SIMBANKA": 0.5, "SIMBANKB": 0.5}}
    )
    assert ok.status_code == 200
    reb = (await admin_client.get(f"{API}/portfolios/{pid}/rebalance?threshold_pp=1")).json()
    assert reb["basis"] == "custom targets" and reb["suggestions"]

    csv_text = (await admin_client.get(f"{API}/portfolios/{pid}/export")).text
    assert csv_text.splitlines()[0].startswith("date,symbol,exchange,side")
    pid2 = (await admin_client.post(f"{API}/portfolios", json={"name": "Copy"})).json()["id"]
    files = {"file": ("t.csv", csv_text, "text/csv")}
    dry = (await admin_client.post(f"{API}/portfolios/{pid2}/import?dry_run=true", files=files)).json()
    assert dry["valid_rows"] == 3 and dry["imported"] == 0
    done = (await admin_client.post(f"{API}/portfolios/{pid2}/import", files=files)).json()
    assert done["imported"] == 3
    copy = (await admin_client.get(f"{API}/portfolios/{pid2}")).json()
    assert abs(copy["realized_pnl"] - s["realized_pnl"]) < 1e-6
    broken = "date,symbol,side,quantity,price\n2024-01-01,NOPE,BUY,1,1\n2024-01-01,SIMBANKA,HOLD,1,1\n"
    res = (
        await admin_client.post(f"{API}/portfolios/{pid2}/import", files={"file": ("b.csv", broken, "text/csv")})
    ).json()
    assert res["imported"] == 0 and res["error_count"] == 2

    txns = (await admin_client.get(f"{API}/portfolios/{pid}/transactions")).json()
    first_buy = next(t for t in txns if t["symbol"] == "SIMBANKA" and t["side"] == "BUY")
    r = await admin_client.delete(f"{API}/portfolios/{pid}/transactions/{first_buy['id']}")
    assert r.status_code == 422  # would leave the later sell uncovered

    me = (await admin_client.get(f"{API}/auth/me")).json()
    async with get_sessionmaker()() as db:
        out, err, sources = await ai_tools.execute(db, me["id"], "get_portfolio_risk", {"portfolio": "Core"})
    assert not err and json.loads(out)["data"]["portfolio"] == "Core" and sources
    async with get_sessionmaker()() as db:
        out, err, _ = await ai_tools.execute(db, me["id"], "get_portfolio_risk", {"portfolio": "missing"})
    assert err

    sizing = (
        await admin_client.post(
            f"{API}/risk/position-size",
            json={"capital": 500000, "risk_pct": 1, "entry": 250, "stop": 240, "target": 280},
        )
    ).json()
    assert sizing["quantity"] == 500 and sizing["risk_reward"] == 3.0

    created = await admin_client.post(
        f"{API}/users", json={"email": "other@example.com", "password": "AnalystPass123", "role": "analyst"}
    )
    assert created.status_code == 201
    admin_client.cookies.clear()
    r = await admin_client.post(f"{API}/auth/login", json={"email": "other@example.com", "password": "AnalystPass123"})
    admin_client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert (await admin_client.get(f"{API}/portfolios")).json() == []
    assert (await admin_client.get(f"{API}/portfolios/{pid}")).status_code == 404


async def test_journal_stats_and_ai_coach(admin_client: AsyncClient, fake_claude) -> None:
    await setup_simulated(admin_client)
    base = datetime.now(UTC) - timedelta(days=30)
    pnl = [50, -20, 30, -40, 25, -10]
    for i, p in enumerate(pnl):
        entry = base + timedelta(days=i * 3)
        body = {
            "symbol": "SIMBANKA",
            "direction": "LONG",
            "entry_at": entry.isoformat(),
            "entry_price": 1000,
            "quantity": 10,
            "exit_at": (entry + timedelta(days=1)).isoformat(),
            "exit_price": 1000 + p,
            "strategy": "Breakout" if i % 2 == 0 else "Pullback",
            "brokerage": 10,
            "stop_loss": 980 if i < 4 else None,
            "emotion": "FOMO" if p < 0 else "Calm",
        }
        r = await admin_client.post(f"{API}/journal/trades", json=body)
        assert r.status_code == 201, r.text
    bad = await admin_client.post(
        f"{API}/journal/trades",
        json={
            "symbol": "SIMBANKA",
            "direction": "LONG",
            "entry_at": base.isoformat(),
            "entry_price": 100,
            "quantity": 1,
            "stop_loss": 110,
        },
    )
    assert bad.status_code == 422
    open_ = (
        await admin_client.post(
            f"{API}/journal/trades",
            json={
                "symbol": "SIMBANKB",
                "direction": "SHORT",
                "entry_at": base.isoformat(),
                "entry_price": 200,
                "quantity": 5,
                "stop_loss": 210,
                "target": 180,
            },
        )
    ).json()
    assert open_["status"] == "OPEN" and open_["planned_rr"] == 2.0

    stats = (await admin_client.get(f"{API}/journal/stats")).json()
    st = stats["statistics"]
    assert st["trades"] == 6 and st["open_trades"] == 1 and st["win_rate"] == 0.5
    assert abs(st["net_pnl"] - (sum(pnl) * 10 - 60)) < 1e-6
    assert {f["code"] for f in stats["findings"]} >= {"no_stop_loss", "stop_not_respected"}
    assert {s["strategy"] for s in st["by_strategy"]} == {"Breakout", "Pullback"}

    closed = await admin_client.post(
        f"{API}/journal/trades/{open_['id']}/close", json={"exit_at": datetime.now(UTC).isoformat(), "exit_price": 190}
    )
    assert closed.json()["net_pnl"] == 50 and closed.json()["r_multiple"] == 1.0
    again = await admin_client.post(
        f"{API}/journal/trades/{open_['id']}/close", json={"exit_at": datetime.now(UTC).isoformat(), "exit_price": 190}
    )
    assert again.status_code == 409

    coach = {k: ([] if v.get("type") == "array" else "x") for k, v in COACH_SCHEMA["properties"].items()}
    coach["summary"] = "Losses on FOMO trades outweigh the edge from breakouts."
    fm = fake_claude([msg([text(json.dumps(coach))])])
    r = await admin_client.post(f"{API}/journal/coach")
    assert r.status_code == 201, r.text
    call = fm.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    prompt = call["messages"][0]["content"]
    assert "RULE-BASED FINDINGS" in prompt and "stop_not_respected" in prompt
    latest = (await admin_client.get(f"{API}/journal/coach")).json()
    assert latest["content"]["summary"].startswith("Losses on FOMO")
    assert "trading_journal" in (await admin_client.get(f"{API}/journal/export")).headers["content-disposition"]


async def test_backtest_api(admin_client: AsyncClient) -> None:
    await setup_simulated(admin_client)
    meta = (await admin_client.get(f"{API}/backtests/strategies")).json()
    assert {"ma_crossover", "momentum", "custom"} <= set(meta["strategies"])
    end = date.today() - timedelta(days=1)
    body = {
        "strategy": "ma_crossover",
        "params": {"fast": 10, "slow": 30},
        "universe": {"type": "symbols", "symbols": ["SIMBANKA", "SIMBANKB"]},
        "start": (end - timedelta(days=730)).isoformat(),
        "end": end.isoformat(),
        "max_positions": 2,
        "sizing_value": 50,
        "stop_loss_pct": 8,
    }
    r = await admin_client.post(f"{API}/backtests", json=body)
    assert r.status_code == 201, r.text
    res = r.json()["result"]
    assert res["data_status"] == "SIMULATED" and res["metrics"]["trades"] > 0
    assert any("Hand-picked" in w for w in res["warnings"]) and res["assumptions"]
    assert res["equity_curve"] and "benchmark_return_pct" in res["metrics"]
    # the benchmark covers the whole two-year window (regression: it used to load only ~420 days)
    assert not any("partial" in w or "No benchmark" in w for w in res["warnings"])
    assert res["equity_curve"][0]["benchmark"] is not None

    mom = {
        **body,
        "strategy": "momentum",
        "params": {"lookback": 63, "skip": 5, "top_n": 3},
        "universe": {"type": "index", "index_code": "NIFTY50"},
    }
    r = await admin_client.post(f"{API}/backtests", json=mom)
    assert r.status_code == 201, r.text
    assert any("point-in-time" in w for w in r.json()["result"]["warnings"])

    assert (
        await admin_client.post(f"{API}/backtests", json={**body, "universe": {"type": "symbols", "symbols": ["NOPE"]}})
    ).status_code == 422
    assert (await admin_client.post(f"{API}/backtests", json={**body, "start": body["end"]})).status_code == 422
    assert (
        await admin_client.post(
            f"{API}/backtests",
            json={**body, "strategy": "custom", "params": {"entry": [{"left": "bogus", "op": ">", "right": 1}]}},
        )
    ).status_code == 422

    runs = (await admin_client.get(f"{API}/backtests")).json()
    assert len(runs) == 2 and runs[0]["strategy"] == "momentum"
    assert (await admin_client.delete(f"{API}/backtests/{runs[0]['id']}")).status_code == 204


async def test_alerts_engine_and_notifications(admin_client: AsyncClient, bus: MemoryBus) -> None:
    await setup_simulated(admin_client)
    types = (await admin_client.get(f"{API}/alerts/types")).json()
    assert len(types["types"]) == 12 and types["channels"]["email"] is False
    a = await _inst("SIMBANKA")

    r = await admin_client.post(
        f"{API}/alerts",
        json={
            "alert_type": "price_above",
            "symbol": "SIMBANKA",
            "params": {"price": 1000},
            "cooldown_minutes": 1,
            "channels": ["email"],
        },
    )
    assert r.status_code == 201, r.text
    alert = r.json()
    assert alert["channels"] == ["in_app", "email"] and "above" in alert["description"]
    assert (
        await admin_client.post(
            f"{API}/alerts", json={"alert_type": "price_above", "symbol": "SIMBANKA", "params": {"price": -1}}
        )
    ).status_code == 422
    assert (
        await admin_client.post(
            f"{API}/alerts", json={"alert_type": "portfolio_drawdown", "params": {"threshold_pct": 10}}
        )
    ).status_code == 422

    pushed: list[dict] = []

    me = (await admin_client.get(f"{API}/auth/me")).json()

    async def evaluate() -> int:
        async with get_sessionmaker()() as db:
            return await evaluate_alerts(db, me["id"])

    await _quote(bus, a, 900)
    assert (await admin_client.post(f"{API}/alerts/evaluate")).json() == {"triggered": 0}
    await _quote(bus, a, 1100)
    assert await evaluate() == 1
    assert await evaluate() == 0  # still above: edge-triggered, no repeat
    await _quote(bus, a, 950)
    assert await evaluate() == 0  # re-armed

    notes = (await admin_client.get(f"{API}/notifications")).json()
    assert notes["unread"] == 1
    n = notes["items"][0]
    assert n["alert_id"] == alert["id"] and n["data"]["data_status"] == "SIMULATED" and n["data"]["price"] == 1100
    assert n["data"]["delivery"]["email"].startswith("failed: Email notifications are disabled")
    assert (await admin_client.post(f"{API}/notifications/{n['id']}/read")).status_code == 200
    assert (await admin_client.get(f"{API}/notifications?unread_only=true")).json()["unread"] == 0

    once = (
        await admin_client.post(
            f"{API}/alerts",
            json={
                "alert_type": "pct_change",
                "symbol": "SIMBANKA",
                "params": {"threshold_pct": 3, "direction": "down"},
                "trigger_once": True,
            },
        )
    ).json()
    await _quote(bus, a, 900, prev_close=950)
    assert await evaluate() == 1
    listed = {x["id"]: x for x in (await admin_client.get(f"{API}/alerts")).json()}
    assert listed[once["id"]]["is_active"] is False

    news = (
        await admin_client.post(
            f"{API}/alerts",
            json={"alert_type": "announcement", "symbol": "SIMBANKA", "params": {"keywords": ["dividend"]}},
        )
    ).json()
    async with get_sessionmaker()() as db:
        art = NewsArticle(
            source_name="Test wire",
            url="https://example.com/a",
            url_hash="h1",
            title_hash="t1",
            title="SIMBANKA declares interim dividend",
            published_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        db.add(art)
        await db.flush()
        db.add(NewsArticleInstrument(article_id=art.id, instrument_id=a.id))
        await db.commit()
    assert await evaluate() == 1
    latest = (await admin_client.get(f"{API}/notifications")).json()["items"][0]
    assert latest["alert_id"] == news["id"] and "dividend" in latest["title"]
    assert await evaluate() == 0  # article already seen

    async def collect() -> None:
        async for channel, message in bus.subscribe(CH_NOTIFICATIONS):
            pushed.append({"channel": channel, **message})
            return

    import asyncio

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await _quote(bus, a, 1300)
    await admin_client.patch(f"{API}/alerts/{alert['id']}", json={"cooldown_minutes": 1})
    async with get_sessionmaker()() as db:
        from app.db.models import Alert

        row = await db.get(Alert, alert["id"])
        row.last_triggered_at = None
        await db.commit()
    await _quote(bus, a, 900)
    await evaluate()
    await _quote(bus, a, 1300)
    assert await evaluate() == 1
    await asyncio.wait_for(task, 2)
    assert pushed and pushed[0]["title"].startswith("SIMBANKA rose above")

    test = (await admin_client.post(f"{API}/admin/notifications/test", json={"channel": "telegram"})).json()
    assert test["ok"] is False and "disabled" in test["error"]
    assert (await admin_client.post(f"{API}/notifications/read-all")).json()["updated"] >= 1
    assert (await admin_client.delete(f"{API}/alerts/{alert['id']}")).status_code == 204


async def test_telegram_delivery_never_leaks_token(admin_client: AsyncClient) -> None:
    await admin_client.put(
        f"{API}/admin/settings/notifications", json={"value": {"telegram": {"enabled": True, "chat_id": "-100123"}}}
    )
    await admin_client.put(f"{API}/admin/settings-secrets", json={"telegram_bot_token": "123:SECRET-TOKEN"})
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    async with get_sessionmaker()() as db, httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        try:
            await notify.send_telegram(db, "hello", client=c)
        except notify.DeliveryError as exc:
            assert "SECRET" not in str(exc) and "401" in str(exc)
        else:
            raise AssertionError("expected DeliveryError")
    assert "SECRET-TOKEN" in seen["url"] and seen["body"]["chat_id"] == "-100123"
