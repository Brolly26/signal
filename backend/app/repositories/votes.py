"""Vote persistence."""

from uuid import UUID

import asyncpg

# ON CONFLICT DO NOTHING rather than a SELECT-then-INSERT: the unique index
# decides, so two simultaneous votes from the same browser cannot both land.
# A NULL return means the vote already existed.
_INSERT_SQL = """
INSERT INTO request_votes (request_id, voter_key)
VALUES ($1, $2)
ON CONFLICT (request_id, voter_key) DO NOTHING
RETURNING id
"""

_DELETE_SQL = """
DELETE FROM request_votes
 WHERE request_id = $1 AND voter_key = $2
RETURNING id
"""

_COUNT_SQL = "SELECT count(*) FROM request_votes WHERE request_id = $1"

_HAS_VOTED_SQL = """
SELECT EXISTS (
    SELECT 1 FROM request_votes WHERE request_id = $1 AND voter_key = $2
)
"""

_REQUEST_EXISTS_SQL = "SELECT EXISTS (SELECT 1 FROM feature_requests WHERE id = $1)"


async def request_exists(connection: asyncpg.Connection, request_id: UUID) -> bool:
    return await connection.fetchval(_REQUEST_EXISTS_SQL, request_id)


async def add_vote(
    connection: asyncpg.Connection, request_id: UUID, voter_key: str
) -> bool:
    """Record a vote. False means this voter had already voted."""
    return await connection.fetchval(_INSERT_SQL, request_id, voter_key) is not None


async def remove_vote(
    connection: asyncpg.Connection, request_id: UUID, voter_key: str
) -> bool:
    """Withdraw a vote. False means there was nothing to withdraw."""
    return await connection.fetchval(_DELETE_SQL, request_id, voter_key) is not None


async def count_votes(connection: asyncpg.Connection, request_id: UUID) -> int:
    return await connection.fetchval(_COUNT_SQL, request_id)


async def has_voted(
    connection: asyncpg.Connection, request_id: UUID, voter_key: str
) -> bool:
    return await connection.fetchval(_HAS_VOTED_SQL, request_id, voter_key)
