"""Feature request business rules.

Thin by design in this phase: it owns the unit of work (which connection, which
transaction) and the mapping between records and schemas, so routes stay free of
SQL and repositories stay free of HTTP.

Creating a request commits it first and only then queues an analysis, so an
AI failure can never cost a submission. The analysis row is created in the same
transaction as nothing else: if no provider is configured, none is created at
all and the request reports `not_requested`, because a pending row that nothing
can process would be a lie told by the database.
"""

from uuid import UUID

import asyncpg

from fastapi import HTTPException, status

from app.core.config import Settings
from app.repositories import requests as requests_repository
from app.schemas.request import (
    FeatureRequestCreate,
    FeatureRequestDetail,
    FeatureRequestOut,
    FeatureRequestPage,
    RequestSort,
)
from app.services import analyses as analyses_service
from app.services import review as review_service
from app.services.analyses import AnalysisJob


def normalise_query(raw: str | None) -> str | None:
    """A blank or whitespace-only search is the same as no search."""
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


async def create_request(
    pool: asyncpg.Pool,
    payload: FeatureRequestCreate,
    *,
    settings: Settings,
) -> tuple[FeatureRequestOut, AnalysisJob | None]:
    """Persist the request, then queue its analysis.

    Returns the job so the route can hand it to a background task: the service
    layer stays free of the web framework's scheduling.
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            record = await requests_repository.insert_request(
                connection,
                title=payload.title,
                description=payload.description,
                requester_label=payload.requester_label,
            )
            job = await analyses_service.queue_analysis(
                connection,
                request_id=record["id"],
                title=record["title"],
                description=record["description"],
                settings=settings,
            )

    created = FeatureRequestOut.model_validate(dict(record))
    # The queued row exists, so the board already shows `pending`.
    if job is not None:
        created = created.model_copy(update={"analysis_state": "pending"})
    return created, job


async def get_request(pool: asyncpg.Pool, request_id: UUID) -> FeatureRequestDetail:
    """A single request with its most recent analysis. 404 if it does not exist."""
    async with pool.acquire() as connection:
        record = await requests_repository.get_request(connection, request_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature request not found.")

    analysis = await analyses_service.get_latest_for_request(pool, request_id)
    links = await review_service.get_links(pool, request_id)
    return FeatureRequestDetail(**dict(record), analysis=analysis, links=links)


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
