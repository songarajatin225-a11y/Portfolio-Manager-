from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import AuditLog
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, register_and_login


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"
    ready = await client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "bus": "ok"}


async def test_first_user_becomes_admin_and_registration_closes(client: AsyncClient) -> None:
    status = (await client.get("/api/v1/auth/bootstrap-status")).json()
    assert status["needs_setup"] is True
    data = await register_and_login(client)
    assert data["user"]["role"] == "admin"
    # second registration is refused when public registration is disabled
    client.cookies.clear()
    r = await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "AnotherPass123"})
    assert r.status_code == 403
    assert (await client.get("/api/v1/auth/bootstrap-status")).json()["needs_setup"] is False


async def test_login_sets_httponly_cookies_and_me_works(client: AsyncClient) -> None:
    await register_and_login(client)
    client.cookies.clear()
    r = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    set_cookies = r.headers.get_list("set-cookie")
    access = next(c for c in set_cookies if c.startswith("mi_access="))
    refresh = next(c for c in set_cookies if c.startswith("mi_refresh="))
    assert "HttpOnly" in access and "HttpOnly" in refresh
    assert "Path=/api/v1/auth" in refresh
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == ADMIN_EMAIL
    assert "password_hash" not in me.json()


async def test_wrong_password_is_rejected_and_audited(client: AsyncClient, db) -> None:
    await register_and_login(client)
    client.cookies.clear()
    r = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"
    actions = (await db.scalars(select(AuditLog.action))).all()
    assert "auth.login_failed" in actions


async def test_unauthenticated_requests_are_rejected(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_csrf_required_for_cookie_mutations(client: AsyncClient) -> None:
    await register_and_login(client)
    csrf = client.headers.pop("X-CSRF-Token")
    r = await client.patch("/api/v1/auth/me/preferences", json={"preferences": {"theme": "dark"}})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"
    client.headers["X-CSRF-Token"] = csrf
    r = await client.patch("/api/v1/auth/me/preferences", json={"preferences": {"theme": "dark"}})
    assert r.status_code == 200
    assert r.json()["preferences"]["theme"] == "dark"


async def test_bearer_token_flow_skips_csrf(client: AsyncClient) -> None:
    await register_and_login(client)
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token")
    tok = await client.post("/api/v1/auth/token", data={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert tok.status_code == 200
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    r = await client.patch("/api/v1/auth/me/preferences", json={"preferences": {"x": 1}}, headers=headers)
    assert r.status_code == 200


async def test_refresh_rotation_and_reuse_detection(client: AsyncClient) -> None:
    await register_and_login(client)
    old_refresh = client.cookies.get("mi_refresh")
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    new_refresh = client.cookies.get("mi_refresh")
    assert new_refresh and new_refresh != old_refresh
    # replaying the old token revokes the whole family
    client.cookies.set("mi_refresh", old_refresh, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "refresh_reused"
    client.cookies.set("mi_refresh", new_refresh, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401


async def test_logout_revokes_session(client: AsyncClient) -> None:
    await register_and_login(client)
    refresh = client.cookies.get("mi_refresh")
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 204
    client.cookies.set("mi_refresh", refresh, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_weak_password_rejected(client: AsyncClient) -> None:
    r = await client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "alllowercase1"})
    assert r.status_code == 422


async def test_admin_user_management_and_rbac(admin_client: AsyncClient) -> None:
    r = await admin_client.post(
        "/api/v1/users", json={"email": "viewer@example.com", "password": "ViewerPass123", "role": "viewer"}
    )
    assert r.status_code == 201
    viewer_id = r.json()["id"]
    assert len((await admin_client.get("/api/v1/users")).json()) == 2

    # viewer cannot list users
    admin_client.cookies.clear()
    login = await admin_client.post(
        "/api/v1/auth/login", json={"email": "viewer@example.com", "password": "ViewerPass123"}
    )
    admin_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert (await admin_client.get("/api/v1/users")).status_code == 403

    # back to admin: deactivate the viewer, who can then no longer log in
    admin_client.cookies.clear()
    login = await admin_client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    r = await admin_client.patch(f"/api/v1/users/{viewer_id}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    admin_client.cookies.clear()
    r = await admin_client.post("/api/v1/auth/login", json={"email": "viewer@example.com", "password": "ViewerPass123"})
    assert r.status_code == 401


async def test_ws_ticket_issued(admin_client: AsyncClient) -> None:
    r = await admin_client.post("/api/v1/auth/ws-ticket")
    assert r.status_code == 200
    assert r.json()["expires_in"] == 60
