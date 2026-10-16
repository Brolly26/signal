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

from collections.abc import Sequence
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


# --------------------------------------------------------------- completion ---

# Both writes are conditional on the row still being pending. A worker that
# wakes up late cannot overwrite an analysis that was already failed by the
# startup sweep or superseded by a retry.
_COMPLETE_SQL = """
UPDATE ai_analyses
   SET status                = 'completed',
       classification        = $2,
       recommendation        = $3,
       underlying_need       = $4,
       problem_summary       = $5,
       suggested_theme       = $6,
       reasoning             = $7,
       model_name            = $8,
       retrieval_mode        = $9,
       tool_calls_count      = $10,
       fabricated_refs_count = $11,
       latency_ms            = $12,
       completed_at          = now()
 WHERE id = $1 AND status = 'pending'
RETURNING id
"""

_FAIL_SQL = """
UPDATE ai_analyses
   SET status       = 'failed',
       error_code   = $2,
       model_name   = coalesce($3, model_name),
       latency_ms   = coalesce($4, latency_ms),
       completed_at = now()
 WHERE id = $1 AND status = 'pending'
RETURNING id
"""

_INSERT_CANDIDATE_SQL = """
INSERT INTO analysis_related_requests
       (analysis_id, related_request_id, relationship_type, explanation)
VALUES ($1, $2, $3, $4)
"""


async def complete_analysis(
    connection: asyncpg.Connection,
    analysis_id: UUID,
    *,
    classification: str,
    recommendation: str,
    underlying_need: str,
    problem_summary: str,
    suggested_theme: str,
    reasoning: str,
    model_name: str,
    retrieval_mode: str,
    tool_calls_count: int,
    fabricated_refs_count: int,
    latency_ms: int,
    candidates: Sequence[tuple[UUID, str, str]],
) -> bool:
    """Store a finished analysis and its candidates in one transaction.

    False means the row was no longer pending, so the result is discarded.
    """
    async with connection.transaction():
        stored = await connection.fetchval(
            _COMPLETE_SQL,
            analysis_id,
            classification,
            recommendation,
            underlying_need,
            problem_summary,
            suggested_theme,
            reasoning,
            model_name,
            retrieval_mode,
            tool_calls_count,
            fabricated_refs_count,
            latency_ms,
        )
        if stored is None:
            return False

        for related_request_id, relationship_type, explanation in candidates:
            await connection.execute(
                _INSERT_CANDIDATE_SQL,
                analysis_id,
                related_request_id,
                relationship_type,
                explanation,
            )
    return True


async def fail_analysis(
    connection: asyncpg.Connection,
    analysis_id: UUID,
    error_code: str,
    *,
    model_name: str | None = None,
    latency_ms: int | None = None,
) -> bool:
    """Record a failure. False means the row was no longer pending."""
    return (
        await connection.fetchval(_FAIL_SQL, analysis_id, error_code, model_name, latency_ms)
        is not None
    )


# ------------------------------------------------------------------- reading ---

_SELECT_ANALYSIS_SQL = """
SELECT id, request_id, status, classification, recommendation, underlying_need,
       problem_summary, suggested_theme, reasoning, model_name, retrieval_mode,
       tool_calls_count, fabricated_refs_count, latency_ms, error_code,
       created_at, started_at, completed_at
  FROM ai_analyses
 WHERE id = $1
"""

_SELECT_LATEST_ID_SQL = """
SELECT id FROM ai_analyses
 WHERE request_id = $1
 ORDER BY created_at DESC, id DESC
 LIMIT 1
"""

_SELECT_CANDIDATES_SQL = """
SELECT ar.related_request_id AS request_id,
       r.title,
       ar.relationship_type,
       ar.explanation,
       (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS vote_count
  FROM analysis_related_requests AS ar
  JOIN feature_requests AS r ON r.id = ar.related_request_id
 WHERE ar.analysis_id = $1
 ORDER BY ar.relationship_type, r.title
"""


async def get_analysis(
    connection: asyncpg.Connection, analysis_id: UUID
) -> asyncpg.Record | None:
    return await connection.fetchrow(_SELECT_ANALYSIS_SQL, analysis_id)


async def get_latest_analysis_id(
    connection: asyncpg.Connection, request_id: UUID
) -> UUID | None:
    return await connection.fetchval(_SELECT_LATEST_ID_SQL, request_id)


async def get_candidates(
    connection: asyncpg.Connection, analysis_id: UUID
) -> list[asyncpg.Record]:
    return list(await connection.fetch(_SELECT_CANDIDATES_SQL, analysis_id))
