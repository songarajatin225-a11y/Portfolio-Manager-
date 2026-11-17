from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/marketintel_test"
)
os.environ["REDIS_URL"] = ""
os.environ["ALLOW_SIMULATED_FEED"] = "true"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = ""
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "1000"

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.bus import MemoryBus, set_bus  # noqa: E402
from app.db import models  # noqa: E402, F401
from app.db.base import Base  # noqa: E402
from app.db.session import dispose_engine, get_engine, get_sessionmaker  # noqa: E402

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Sup3rSecretPass"


@pytest.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
async def _clean_tables(_schema: None) -> AsyncIterator[None]:
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def bus() -> MemoryBus:
    b = MemoryBus()
    set_bus(b)
    return b


@pytest.fixture
async def db():
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client(bus: MemoryBus) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


async def register_and_login(
    client: AsyncClient, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD, full_name: str = "Admin"
) -> dict:
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": password, "full_name": full_name})
    assert r.status_code == 201, r.text
    data = r.json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    return data


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    await register_and_login(client)
    return client
