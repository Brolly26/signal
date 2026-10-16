"""Health endpoint.

Reports liveness plus two things that are genuinely useful when something is
broken: whether the database answers, and which schema migration is applied.
It deliberately exposes no credentials, no DSN and no key material.
"""

import logging

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.db.pool import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/api/health")
async def health(response: Response) -> dict[str, object]:
    settings = get_settings()

    database = "unavailable"
    schema_version: str | None = None

    try:
        pool = get_pool()
        async with pool.acquire() as connection:
            await connection.execute("SELECT 1")
            schema_version = await connection.fetchval(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            )
        database = "connected"
    except Exception:
        # The exception may carry the DSN, so it is logged without the message.
        logger.exception("Health check could not reach the database")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if database == "connected" else "degraded",
        "database": database,
        "schema_version": schema_version,
        # Whether a key is present, never the key itself.
        "ai_provider_configured": settings.ai_provider_configured,
        "environment": settings.app_env,
    }
