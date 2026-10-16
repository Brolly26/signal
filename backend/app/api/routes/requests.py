"""Feature request endpoints."""

from fastapi import APIRouter, Query, status

from app.db.pool import get_pool
from app.schemas.request import (
    FeatureRequestCreate,
    FeatureRequestOut,
    FeatureRequestPage,
    RequestSort,
)
from app.services import requests as requests_service

router = APIRouter(prefix="/api/requests", tags=["requests"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_QUERY_LENGTH = 200


@router.post(
    "",
    response_model=FeatureRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a feature request",
)
async def create_request(payload: FeatureRequestCreate) -> FeatureRequestOut:
    return await requests_service.create_request(get_pool(), payload)


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
