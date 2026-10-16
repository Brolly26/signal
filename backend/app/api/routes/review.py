"""Decision Inbox endpoints — the human review surface.

No authentication guards these. The review UI is an open route carrying a
visible notice that this is a demo environment (ADR-014); what production would
require is listed in the README rather than simulated here.
"""

from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from app.db.pool import get_pool
from app.schemas.decision import DecisionCreate, DecisionOut, InboxItem, InboxPage
from app.services import review as review_service

router = APIRouter(prefix="/api", tags=["review"])

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class DecisionHistoryPage(BaseModel):
    items: list[DecisionOut]
    total: int
    limit: int
    offset: int


@router.get(
    "/inbox",
    response_model=InboxPage,
    summary="Analyses awaiting a human decision",
)
async def get_inbox(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> InboxPage:
    """An analysis is queued while any proposed relationship has no decision,
    or while a NEEDS_CLARIFICATION verdict has not been acknowledged."""
    return await review_service.get_inbox(get_pool(), limit=limit, offset=offset)


@router.get(
    "/analyses/{analysis_id}",
    response_model=InboxItem,
    summary="One analysis with its candidates and their current decisions",
)
async def get_analysis(analysis_id: UUID) -> InboxItem:
    return await review_service.get_item(get_pool(), analysis_id)


@router.post(
    "/analyses/{analysis_id}/decisions",
    response_model=InboxItem,
    status_code=status.HTTP_201_CREATED,
    summary="Approve, reject or dismiss",
)
async def create_decision(analysis_id: UUID, payload: DecisionCreate) -> InboxItem:
    """Records the decision and reconciles the approved link.

    Approving never deletes or merges the original requests. Rejecting removes
    the link only if no analysis still holds an effective approval for that
    pair, in either direction.
    """
    return await review_service.record_decision(get_pool(), analysis_id, payload)


@router.get(
    "/decisions",
    response_model=DecisionHistoryPage,
    summary="The decision audit log, newest first",
)
async def get_history(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> DecisionHistoryPage:
    items, total = await review_service.get_history(
        get_pool(), limit=limit, offset=offset
    )
    return DecisionHistoryPage(items=items, total=total, limit=limit, offset=offset)
