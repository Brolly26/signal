"""Feature request business rules.

Thin by design in this phase: it owns the unit of work (which connection, which
transaction) and the mapping between records and schemas, so routes stay free of
SQL and repositories stay free of HTTP.

No AI analysis is queued here. The orchestrator does not exist yet, so queuing
one would leave a `pending` row that nothing will ever process. Requests simply
report `not_requested` until the analysis phase lands.
"""

import asyncpg

from app.repositories import requests as requests_repository
from app.schemas.request import (
    FeatureRequestCreate,
    FeatureRequestOut,
    FeatureRequestPage,
    RequestSort,
)


def normalise_query(raw: str | None) -> str | None:
    """A blank or whitespace-only search is the same as no search."""
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


async def create_request(
    pool: asyncpg.Pool,
    payload: FeatureRequestCreate,
) -> FeatureRequestOut:
    async with pool.acquire() as connection:
        record = await requests_repository.insert_request(
            connection,
            title=payload.title,
            description=payload.description,
            requester_label=payload.requester_label,
        )
    return FeatureRequestOut.model_validate(dict(record))


async def list_requests(
    pool: asyncpg.Pool,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
    sort: RequestSort = "recent",
) -> FeatureRequestPage:
    search = normalise_query(query)

    async with pool.acquire() as connection:
        # REPEATABLE READ, not just read-only: under the default READ COMMITTED
        # every statement takes a fresh snapshot, so a row committed between the
        # two queries would make `total` disagree with `items`. The isolation
        # level is what makes them consistent; `readonly` only states intent.
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            records = await requests_repository.list_requests(
                connection, limit=limit, offset=offset, query=search, sort=sort
            )
            total = await requests_repository.count_requests(connection, query=search)

    return FeatureRequestPage(
        items=[FeatureRequestOut.model_validate(dict(record)) for record in records],
        total=total,
        limit=limit,
        offset=offset,
        query=search,
        sort=sort,
    )
