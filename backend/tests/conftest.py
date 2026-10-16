"""Shared test fixtures.

Integration tests run against a real PostgreSQL instance, because the point of
most of them is to verify constraints and indexes that only Postgres enforces.
Each test gets a connection inside a transaction that is always rolled back, so
tests never leave rows behind and never depend on execution order.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(autouse=True)
def _no_ambient_ai_provider(settings, monkeypatch):
    """Keep the suite hermetic: no test may reach the network by accident.

    This exists because of a real incident. Once a key was configured in
    backend/.env, `POST /api/requests` started queueing a genuine analysis in
    its background task, so running the test suite made live calls to
    api.anthropic.com - slow, non-deterministic and billable - and the two
    tests that assert the no-provider behaviour began to fail.

    Tests that need a provider opt in explicitly through a fixture that also
    installs a scripted client, so the only way to reach the real API from the
    suite is to ask for it deliberately.
    """
    monkeypatch.setattr(settings, "anthropic_api_key", None)


@pytest.fixture(scope="session")
def client() -> AsyncIterator[TestClient]:
    """TestClient as a context manager so the app lifespan really runs."""
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def connection(settings) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    transaction = conn.transaction()
    await transaction.start()
    try:
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


@pytest_asyncio.fixture
async def make_request(connection) -> Callable[..., Awaitable[UUID]]:
    """Insert a valid feature request and return its id."""

    counter = {"n": 0}

    async def _make(
        title: str | None = None,
        description: str | None = None,
    ) -> UUID:
        counter["n"] += 1
        n = counter["n"]
        return await connection.fetchval(
            """
            INSERT INTO feature_requests (title, description)
            VALUES ($1, $2) RETURNING id
            """,
            title or f"Test request number {n}",
            description or f"A description long enough to pass validation, item {n}.",
        )

    return _make
