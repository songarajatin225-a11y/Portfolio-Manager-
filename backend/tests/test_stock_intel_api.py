"""Data imports, metrics refresh, screener, stock research and market overview endpoints."""

from __future__ import annotations

from httpx import AsyncClient

from app.api.v1.data import run_metrics_refresh
from tests.test_market_api import setup_simulated

FUND_CSV = (
    "symbol,period_type,period_end,revenue,ebitda,ebit,net_profit,eps,total_equity,total_debt,total_assets,"
    "current_liabilities,operating_cash_flow,capex,shares_outstanding,dividend_per_share,source,reported_at\n"
    "SIMBANKA,A,2026-03-31,10000,3000,2500,2000,20,15000,5000,40000,8000,2500,500,100,5,Test filing FY26,2026-05-10\n"
    "SIMBANKA,A,2025-03-31,9000,2700,2200,1600,16,13500,5000,36000,7000,2000,400,100,4,Test filing FY25,2025-05-10\n"
    "SIMBANKB,A,2026-03-31,500,100,150,40,4,300,10,900,100,50,10,10,0,Bad filing,2026-05-10\n"
    "NOPE,A,2026-03-31,1,1,1,1,1,1,1,1,1,1,1,1,1,x,2026-05-10\n"
)


async def _upload(client: AsyncClient, dataset: str, csv: str) -> dict:
    r = await client.post(f"/api/v1/admin/data-import/{dataset}", files={"file": (f"{dataset}.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


async def test_imports_ratios_metrics_screener_and_stock_endpoints(admin_client: AsyncClient) -> None:
    await setup_simulated(admin_client)

    res = await _upload(admin_client, "fundamentals", FUND_CSV)
    assert res["imported"] == 3 and res["rejected"] == 1 and "unknown symbol" in res["errors"][0]["error"]
    assert any("ebit exceeds ebitda" in w["warning"] for w in res["warnings"])  # SIMBANKB stored but excluded

    sh = await _upload(
        admin_client,
        "shareholding",
        "symbol,period_end,promoter_pct,fii_pct,dii_pct,public_pct,source\n"
        "SIMBANKA,2026-09-30,50,20,15,15,Test SHP\nSIMBANKA,2026-06-30,60,30,20,10,Bad\n",
    )
    assert sh["imported"] == 1 and "sum to" in sh["errors"][0]["error"]
    flows = await _upload(
        admin_client,
        "institutional_flows",
        "date,category,buy_value,sell_value,source\n2026-11-14,FII,1000,1500,Test\n2026-11-14,XYZ,1,1,T\n",
    )
    assert flows["imported"] == 1
    deals = await _upload(
        admin_client,
        "bulk_block_deals",
        "date,symbol,deal_type,client_name,side,quantity,price,source\n"
        "2026-11-10,SIMBANKA,BULK,Example Fund,BUY,100000,1500,Test\n",
    )
    assert deals["imported"] == 1
    candles = await _upload(
        admin_client,
        "candles",
        "symbol,date,open,high,low,close,volume\n"
        "SIMBANKA,2020-01-02,10,11,9,10.5,1000\n"
        "SIMBANKA,2020-01-03,10,9,9.5,10.5,1000\n",
    )
    assert (
        candles["imported"] == 1 and candles["candles_stored"] == 1 and "inconsistent" in candles["errors"][0]["error"]
    )
    tmpl = await admin_client.get("/api/v1/admin/data-import/templates/fundamentals")
    assert tmpl.text.startswith("symbol,period_type")

    # fundamentals endpoint uses only the validated periods, with source and reporting date
    f = (await admin_client.get("/api/v1/stocks/SIMBANKA/fundamentals")).json()
    assert f["ratios"]["periods_used"] == 2
    roe = f["ratios"]["ratios"]["roe"]
    assert roe["value"] is not None and roe["source"] == "Test filing FY26" and roe["reported_at"] == "2026-05-10"
    assert f["ratios"]["ratios"]["revenue_growth"]["value"] == 11.1111
    bad = (await admin_client.get("/api/v1/stocks/SIMBANKB/fundamentals")).json()
    assert bad["ratios"]["periods_rejected"] == 1 and bad["periods"][0]["validation_errors"]

    inst = (await admin_client.get("/api/v1/stocks/SIMBANKA/institutional")).json()
    assert inst["shareholding"][0]["promoter_pct"] == 50 and inst["deals"][0]["client_name"] == "Example Fund"
    fl_resp = await admin_client.get("/api/v1/market/institutional-flows?days=730")
    assert fl_resp.status_code == 200, fl_resp.text
    fl = fl_resp.json()
    assert fl["items"][0]["net_value"] == -500

    # metrics refresh over the simulated universe (backfills daily candles from the provider)
    n = await run_metrics_refresh()
    assert n >= 30
    ov = (await admin_client.get("/api/v1/market/overview")).json()
    b = ov["breadth"]
    assert ov["universe_size"] == 32 and b["advances"] + b["declines"] + b["unchanged"] == ov["priced"]
    assert ov["sectors"] and ov["data_statuses"] == ["SIMULATED"]
    assert ov["metrics_refresh"]["state"] == "completed"
    hm = (await admin_client.get("/api/v1/market/heatmap")).json()
    assert len(hm["tiles"]) == 32 and {t["sector"] for t in hm["tiles"]} >= {"Banking", "Pharma"}
    # only SIMBANKA has a market cap, so every tile must use one common basis (turnover)
    assert hm["weight_basis"] == "turnover" and {t["weight_basis"] for t in hm["tiles"]} == {"turnover"}

    fields = (await admin_client.get("/api/v1/screener/fields")).json()
    assert any(f["key"] == "roe" for f in fields["fields"]) and fields["presets"]
    all_rows = (await admin_client.post("/api/v1/screener/run", json={"filters": [], "limit": 500})).json()
    assert all_rows["universe"] == 32 and all_rows["matched"] == 32
    banks = (
        await admin_client.post(
            "/api/v1/screener/run",
            json={"filters": [{"field": "sector", "op": "eq", "value": "Banking"}], "sort": "ret_3m"},
        )
    ).json()
    assert banks["matched"] == 4 and all(r["sector"] == "Banking" for r in banks["results"])
    rsi = (
        await admin_client.post(
            "/api/v1/screener/run",
            json={
                "filters": [{"field": "rsi_14", "op": "between", "value": 0, "value2": 50}],
                "sort": "rsi_14",
                "direction": "asc",
            },
        )
    ).json()
    values = [r["values"]["rsi_14"] for r in rsi["results"]]
    assert values == sorted(values) and all(v <= 50 for v in values)
    roe_screen = (
        await admin_client.post("/api/v1/screener/run", json={"filters": [{"field": "roe", "op": "gt", "value": 10}]})
    ).json()
    assert [r["symbol"] for r in roe_screen["results"]] == ["SIMBANKA"]
    assert (
        await admin_client.post("/api/v1/screener/run", json={"filters": [{"field": "nope", "op": "gt", "value": 1}]})
    ).status_code == 422

    saved = await admin_client.post(
        "/api/v1/screener/saved",
        json={"name": "High ROE", "definition": {"filters": [{"field": "roe", "op": "gt", "value": 10}]}},
    )
    assert saved.status_code == 201
    assert (await admin_client.get("/api/v1/screener/saved")).json()[0]["name"] == "High ROE"
    assert (await admin_client.delete(f"/api/v1/screener/saved/{saved.json()['id']}")).status_code == 204

    tech = (await admin_client.get("/api/v1/stocks/SIMBANKA/technicals?interval=1d")).json()
    assert tech["data_status"] == "SIMULATED" and len(tech["series"]["ts"]) == len(tech["series"]["rsi_14"])
    assert tech["latest"]["rsi_14"] is not None and tech["signals"]
    assert "support" in tech["levels"] and tech["volume_profile"]
    intraday = (await admin_client.get("/api/v1/stocks/SIMBANKA/technicals?interval=15m")).json()
    assert "vwap" in intraday["series"]
    perf = (await admin_client.get("/api/v1/stocks/SIMBANKA/performance")).json()
    assert perf["returns"]["1Y"] is not None and perf["max_drawdown_pct"] <= 0
