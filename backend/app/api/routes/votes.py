"""Support (vote) endpoints.

Supporting is idempotent: `201` when the vote is newly recorded, `200` when this
identity had already supported the request. Both carry the authoritative count,
so a double click or a stale tab cannot desynchronise the UI. The duplicate row
itself is prevented by `UNIQUE (request_id, voter_key)` in the database.
"""

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.db.pool import get_pool
from app.schemas.vote import VoteResult, VoterKey
from app.services import votes as votes_service

router = APIRouter(prefix="/api/requests/{request_id}/votes", tags=["votes"])


@router.post("", response_model=VoteResult, summary="Support a request")
async def support_request(
    request_id: UUID,
    voter_key: VoterKey,
    response: Response,
) -> VoteResult:
    result, created = await votes_service.add_vote(get_pool(), request_id, voter_key)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.delete("", response_model=VoteResult, summary="Withdraw support")
async def withdraw_support(request_id: UUID, voter_key: VoterKey) -> VoteResult:
    return await votes_service.remove_vote(get_pool(), request_id, voter_key)
