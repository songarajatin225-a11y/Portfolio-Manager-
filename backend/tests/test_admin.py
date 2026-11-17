from __future__ import annotations

from httpx import AsyncClient


async def test_settings_roundtrip_and_secrets_never_returned(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/api/v1/admin/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["sections"]["ai"]["model"] == "claude-opus-5-5"
    assert body["infrastructure"]["database"]["name"] == "marketintel_test"
    assert "password" not in str(body["infrastructure"]).lower()

    r = await admin_client.put("/api/v1/admin/settings/refresh", json={"value": {"stale_after_seconds": 45}})
    assert r.status_code == 200
    assert r.json()["stale_after_seconds"] == 45
    assert r.json()["metrics_refresh_minutes"] == 15  # defaults preserved

    r = await admin_client.put("/api/v1/admin/settings-secrets", json={"telegram_bot_token": "123:ABC"})
    assert r.json() == {"smtp_password": False, "telegram_bot_token": True}
    full = (await admin_client.get("/api/v1/admin/settings")).text
    assert "123:ABC" not in full

    logs = (await admin_client.get("/api/v1/admin/audit-logs?action=settings")).json()
    assert {i["action"] for i in logs["items"]} == {"settings.update", "settings.secrets_update"}
    assert "123:ABC" not in str(logs)


async def test_unknown_section_404(admin_client: AsyncClient) -> None:
    r = await admin_client.put("/api/v1/admin/settings/nope", json={"value": {}})
    assert r.status_code == 404


async def test_system_status(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/api/v1/system/status")
    assert r.status_code == 200
    names = {s["name"]: s["status"] for s in r.json()["services"]}
    assert names["database"] == "ok"
    assert names["worker"] == "error"  # no worker running in tests
    assert names["market_data"] == "not_configured"
    assert names["ai"] == "not_configured"
