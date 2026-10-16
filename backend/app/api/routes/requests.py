"""Feature request endpoints."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Query, status

from app.core.config import Settings, get_settings
from app.db.pool import get_pool
from app.schemas.request import (
    FeatureRequestCreate,
    FeatureRequestDetail,
    FeatureRequestOut,
    FeatureRequestPage,
    RequestSort,
)
from app.services import analyses as analyses_service
from app.services import requests as requests_service

router = APIRouter(prefix="/api/requests", tags=["requests"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_QUERY_LENGTH = 200


def _schedule(background_tasks: BackgroundTasks, job, settings: Settings) -> None:
    """Hand a queued analysis to a background task.

    `BackgroundTasks` runs in this process, so an analysis does not survive a
    restart — it is recovered as `INTERRUPTED` by the startup sweep and can be
    retried. A durable queue is the production answer and is recorded as future
    work rather than pretended at here.
    """
    background_tasks.add_task(analyses_service.execute_job, get_pool(), job, settings=settings)


@router.post(
    "",
    response_model=FeatureRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a feature request",
)
async def create_request(
    payload: FeatureRequestCreate,
    background_tasks: BackgroundTasks,
) -> FeatureRequestOut:
    settings = get_settings()
    created, job = await requests_service.create_request(
        get_pool(), payload, settings=settings
    )
    # The request is already committed: the analysis is extra, never a condition
    # of the submission succeeding.
    if job is not None:
        _schedule(background_tasks, job, settings)
    return created


@router.get(
    "",
    response_model=FeatureRequestPage,
    summary="List feature requests, with optional search and ordering",
)
async def list_requests(
    q: str | None = Query(
        None,
        max_length=MAX_QUERY_LENGTH,
        description="Search the title and description. Blank is treated as no search.",
    ),
    sort: RequestSort = Query(
        "recent",
        description="`recent` by submission date, `top` by support count.",
    ),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> FeatureRequestPage:
    return await requests_service.list_requests(
        get_pool(), limit=limit, offset=offset, query=q, sort=sort
    )


@router.get(
    "/{request_id}",
    response_model=FeatureRequestDetail,
    summary="One request with its most recent analysis",
)
async def get_request(request_id: UUID) -> FeatureRequestDetail:
    return await requests_service.get_request(get_pool(), request_id)


@router.post(
    "/{request_id}/analyses",
    response_model=FeatureRequestDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the analysis again",
)
async def retry_analysis(
    request_id: UUID,
    background_tasks: BackgroundTasks,
) -> FeatureRequestDetail:
    """Queue a fresh analysis, releasing an interrupted one first.

    `503` when no provider is configured, `404` when the request does not exist,
    `409` while an analysis is genuinely still running.
    """
    settings = get_settings()
    job = await analyses_service.request_retry(get_pool(), request_id, settings=settings)
    _schedule(background_tasks, job, settings)
    return await requests_service.get_request(get_pool(), request_id)
