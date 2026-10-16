"""Support (vote) business rules.

Every operation returns the authoritative count, so a client whose local state
has drifted — cleared browser storage, a second tab, a double click — can
correct itself from the response instead of guessing.

Supporting a request is idempotent. See docs/decisions.md (ADR-015) for why
this replaced the 409 the earlier plan described.
"""

from uuid import UUID

import asyncpg
from asyncpg.exceptions import ForeignKeyViolationError
from fastapi import HTTPException, status

from app.repositories import votes as votes_repository
from app.schemas.vote import VoteResult


async def _result(
    connection: asyncpg.Connection, request_id: UUID, voter_key: str
) -> VoteResult:
    return VoteResult(
        request_id=request_id,
        vote_count=await votes_repository.count_votes(connection, request_id),
        has_voted=await votes_repository.has_voted(connection, request_id, voter_key),
    )


async def _ensure_request_exists(connection: asyncpg.Connection, request_id: UUID) -> None:
    if not await votes_repository.request_exists(connection, request_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature request not found.")


async def add_vote(
    pool: asyncpg.Pool, request_id: UUID, voter_key: str
) -> tuple[VoteResult, bool]:
    """Support a request. Returns the result and whether a row was created.

    Raises 404 if the request does not exist.
    """
    async with pool.acquire() as connection:
        await _ensure_request_exists(connection, request_id)
        try:
            created = await votes_repository.add_vote(connection, request_id, voter_key)
        except ForeignKeyViolationError as exc:
            # The request was deleted between the existence check and the insert.
            # Without this the foreign key would surface as a 503, telling the
            # caller the service is down when the request simply no longer exists.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Feature request not found."
            ) from exc
        return await _result(connection, request_id, voter_key), created


async def remove_vote(pool: asyncpg.Pool, request_id: UUID, voter_key: str) -> VoteResult:
    """Withdraw support.

    Idempotent for the same reason: withdrawing a vote that is not there leaves
    the caller's intent already satisfied, so it is not an error.
    """
    async with pool.acquire() as connection:
        await _ensure_request_exists(connection, request_id)
        await votes_repository.remove_vote(connection, request_id, voter_key)
        return await _result(connection, request_id, voter_key)
