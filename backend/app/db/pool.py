"""asyncpg connection pool lifecycle.

A single pool is created at application startup and closed at shutdown. No
module imports it at call time without going through `get_pool()`, so a missing
pool fails loudly instead of silently opening per-request connections.
"""

import asyncpg

from app.core.config import Settings

_pool: asyncpg.Pool | None = None


async def init_pool(settings: Settings) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=settings.db_command_timeout_seconds,
        # Forwarded to asyncpg.connect(). Must be 0 behind pgbouncer in
        # transaction mode (Supabase pooler on port 6543).
        statement_cache_size=settings.db_statement_cache_size,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Did the app lifespan run?")
    return _pool
