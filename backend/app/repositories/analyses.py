"""Analysis persistence, including interruption recovery.

Two failure modes have to be recovered from, and they are different:

  * the worker started and never finished  -> `started_at` is old
  * the worker never started at all        -> `started_at` is NULL and the row
    has simply been sitting there since `created_at` (the process died between
    inserting the row and picking up the background task)

Both are released as `failed / INTERRUPTED`, which is what makes them visibly
retryable instead of permanently pending.

Concurrency is arbitrated by the partial unique index
`ai_analyses_one_pending_per_request`, not by an application lock: at most one
pending analysis per request can exist. `retry_analysis` therefore releases
stale rows and inserts the new one inside a single transaction — if a genuinely
fresh analysis is still running, the insert raises UniqueViolationError and the
release is rolled back with it.
"""

from uuid import UUID

import asyncpg

_RELEASE_STALE_SQL = """
UPDATE ai_analyses
   SET status       = 'failed',
       error_code   = 'INTERRUPTED',
       completed_at = now()
 WHERE status = 'pending'
   AND ($3::uuid IS NULL OR request_id = $3::uuid)
   AND (
            (started_at IS     NULL AND created_at < now() - make_interval(secs => $2::int))
         OR (started_at IS NOT NULL AND started_at < now() - make_interval(secs => $1::int))
       )
RETURNING id
"""

_INSERT_PENDING_SQL = """
INSERT INTO ai_analyses (request_id, status)
VALUES ($1, 'pending')
RETURNING id
"""

_MARK_STARTED_SQL = """
UPDATE ai_analyses
   SET started_at = now()
 WHERE id = $1 AND status = 'pending'
RETURNING id
"""


async def release_stale_analyses(
    connection: asyncpg.Connection,
    *,
    started_timeout_seconds: int,
    never_started_timeout_seconds: int,
    request_id: UUID | None = None,
) -> list[UUID]:
    """Mark interrupted pending analyses as failed. Returns the released ids.

    With `request_id` omitted this is the startup sweep; with it set it is the
    per-request release performed by a retry.
    """
    rows = await connection.fetch(
        _RELEASE_STALE_SQL,
        started_timeout_seconds,
        never_started_timeout_seconds,
        request_id,
    )
    return [row["id"] for row in rows]


async def create_pending_analysis(connection: asyncpg.Connection, request_id: UUID) -> UUID:
    """Insert a pending analysis.

    Raises asyncpg.exceptions.UniqueViolationError if one is already pending for
    this request.
    """
    return await connection.fetchval(_INSERT_PENDING_SQL, request_id)


async def retry_analysis(
    connection: asyncpg.Connection,
    request_id: UUID,
    *,
    started_timeout_seconds: int,
    never_started_timeout_seconds: int,
) -> tuple[UUID, list[UUID]]:
    """Release any interrupted analysis for this request, then queue a new one.

    Both steps happen in one transaction. Raises UniqueViolationError when a
    non-stale analysis is still in flight, leaving the database untouched; the
    API layer maps that to HTTP 409.
    """
    async with connection.transaction():
        released = await release_stale_analyses(
            connection,
            started_timeout_seconds=started_timeout_seconds,
            never_started_timeout_seconds=never_started_timeout_seconds,
            request_id=request_id,
        )
        analysis_id = await create_pending_analysis(connection, request_id)
    return analysis_id, released


async def mark_analysis_started(connection: asyncpg.Connection, analysis_id: UUID) -> bool:
    """Stamp `started_at`. False means the row is no longer pending."""
    return await connection.fetchval(_MARK_STARTED_SQL, analysis_id) is not None
