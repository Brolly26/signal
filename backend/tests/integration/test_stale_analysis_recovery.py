"""Recovery of analyses left pending by an interrupted backend.

Covers both interruption shapes and the transactional retry:

  * the worker never started (`started_at IS NULL`)
  * the worker started and hung (`started_at` is old)
  * a genuinely fresh analysis must NOT be released
  * retry releases the interrupted row and queues a new one atomically
  * two concurrent retries cannot both queue an analysis
"""

import asyncio

import asyncpg
import pytest
from asyncpg.exceptions import UniqueViolationError

from app.repositories.analyses import (
    mark_analysis_started,
    release_stale_analyses,
    retry_analysis,
)

pytestmark = pytest.mark.asyncio

STARTED_TIMEOUT = 180
NEVER_STARTED_TIMEOUT = 60


async def _insert_pending(connection, request_id, *, created_ago_seconds=0, started_ago_seconds=None):
    """Insert a pending analysis with controllable timestamps."""
    return await connection.fetchval(
        """
        INSERT INTO ai_analyses (request_id, status, created_at, started_at)
        VALUES ($1, 'pending',
                now() - make_interval(secs => $2::int),
                CASE WHEN $3::int IS NULL THEN NULL
                     ELSE now() - make_interval(secs => $3::int) END)
        RETURNING id
        """,
        request_id,
        created_ago_seconds,
        started_ago_seconds,
    )


async def _status_of(connection, analysis_id):
    return await connection.fetchrow(
        "SELECT status, error_code, completed_at FROM ai_analyses WHERE id = $1", analysis_id
    )


# --------------------------------------------------------------- detection ---


async def test_analysis_that_never_started_is_released(connection, make_request):
    request_id = await make_request()
    analysis_id = await _insert_pending(connection, request_id, created_ago_seconds=120)

    released = await release_stale_analyses(
        connection,
        started_timeout_seconds=STARTED_TIMEOUT,
        never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
        request_id=request_id,
    )

    assert released == [analysis_id]
    row = await _status_of(connection, analysis_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "INTERRUPTED"
    assert row["completed_at"] is not None


async def test_analysis_that_started_and_hung_is_released(connection, make_request):
    request_id = await make_request()
    analysis_id = await _insert_pending(
        connection, request_id, created_ago_seconds=400, started_ago_seconds=400
    )

    released = await release_stale_analyses(
        connection,
        started_timeout_seconds=STARTED_TIMEOUT,
        never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
        request_id=request_id,
    )

    assert released == [analysis_id]


async def test_a_recently_created_analysis_is_not_released(connection, make_request):
    """It may simply not have been picked up yet."""
    request_id = await make_request()
    analysis_id = await _insert_pending(connection, request_id, created_ago_seconds=5)

    released = await release_stale_analyses(
        connection,
        started_timeout_seconds=STARTED_TIMEOUT,
        never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
        request_id=request_id,
    )

    assert released == []
    assert (await _status_of(connection, analysis_id))["status"] == "pending"


async def test_an_analysis_still_running_is_not_released(connection, make_request):
    request_id = await make_request()
    analysis_id = await _insert_pending(
        connection, request_id, created_ago_seconds=30, started_ago_seconds=10
    )

    released = await release_stale_analyses(
        connection,
        started_timeout_seconds=STARTED_TIMEOUT,
        never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
        request_id=request_id,
    )

    assert released == []
    assert (await _status_of(connection, analysis_id))["status"] == "pending"


async def test_sweep_without_request_id_covers_every_request(connection, make_request):
    first = await make_request()
    second = await make_request()
    await _insert_pending(connection, first, created_ago_seconds=120)
    await _insert_pending(connection, second, created_ago_seconds=400, started_ago_seconds=400)

    released = await release_stale_analyses(
        connection,
        started_timeout_seconds=STARTED_TIMEOUT,
        never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
    )

    assert len(released) >= 2


# ------------------------------------------------------------------- retry ---


async def test_retry_releases_the_interrupted_analysis_and_queues_a_new_one(
    connection, make_request
):
    request_id = await make_request()
    interrupted = await _insert_pending(connection, request_id, created_ago_seconds=120)

    new_analysis, released = await retry_analysis(
        connection,
        request_id,
        started_timeout_seconds=STARTED_TIMEOUT,
        never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
    )

    assert released == [interrupted]
    assert new_analysis != interrupted
    assert (await _status_of(connection, interrupted))["error_code"] == "INTERRUPTED"
    assert (await _status_of(connection, new_analysis))["status"] == "pending"

    pending = await connection.fetchval(
        "SELECT count(*) FROM ai_analyses WHERE request_id = $1 AND status = 'pending'",
        request_id,
    )
    assert pending == 1


async def test_retry_is_refused_while_an_analysis_is_genuinely_running(connection, make_request):
    request_id = await make_request()
    running = await _insert_pending(
        connection, request_id, created_ago_seconds=20, started_ago_seconds=5
    )

    with pytest.raises(UniqueViolationError):
        await retry_analysis(
            connection,
            request_id,
            started_timeout_seconds=STARTED_TIMEOUT,
            never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
        )

    # The running analysis is untouched: the whole retry rolled back.
    row = await _status_of(connection, running)
    assert row["status"] == "pending"
    assert row["error_code"] is None


async def test_mark_started_only_applies_to_a_pending_analysis(connection, make_request):
    request_id = await make_request()
    analysis_id = await _insert_pending(connection, request_id)

    assert await mark_analysis_started(connection, analysis_id) is True

    await connection.execute(
        """
        UPDATE ai_analyses SET status = 'failed', error_code = 'INTERRUPTED', completed_at = now()
         WHERE id = $1
        """,
        analysis_id,
    )
    # A zombie worker waking up late must not be able to claim the row again.
    assert await mark_analysis_started(connection, analysis_id) is False


# ------------------------------------------------------------- concurrency ---


async def test_two_concurrent_retries_cannot_both_queue_an_analysis(settings):
    """Needs committed data and two real connections, so it cleans up after itself."""
    setup = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    request_id = None
    try:
        request_id = await setup.fetchval(
            """
            INSERT INTO feature_requests (title, description)
            VALUES ('Concurrency probe request',
                    'Inserted by a test to exercise concurrent retry arbitration.')
            RETURNING id
            """
        )
        await _insert_pending(setup, request_id, created_ago_seconds=120)

        first, second = await asyncio.gather(
            asyncpg.connect(dsn=settings.database_url, statement_cache_size=0),
            asyncpg.connect(dsn=settings.database_url, statement_cache_size=0),
        )
        try:
            results = await asyncio.gather(
                retry_analysis(
                    first,
                    request_id,
                    started_timeout_seconds=STARTED_TIMEOUT,
                    never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
                ),
                retry_analysis(
                    second,
                    request_id,
                    started_timeout_seconds=STARTED_TIMEOUT,
                    never_started_timeout_seconds=NEVER_STARTED_TIMEOUT,
                ),
                return_exceptions=True,
            )
        finally:
            await first.close()
            await second.close()

        succeeded = [r for r in results if not isinstance(r, BaseException)]
        failed = [r for r in results if isinstance(r, BaseException)]

        assert len(succeeded) == 1, f"expected exactly one winner, got {results!r}"
        assert len(failed) == 1
        assert isinstance(failed[0], (UniqueViolationError, asyncpg.PostgresError))

        pending = await setup.fetchval(
            "SELECT count(*) FROM ai_analyses WHERE request_id = $1 AND status = 'pending'",
            request_id,
        )
        assert pending == 1
    finally:
        if request_id is not None:
            await setup.execute("DELETE FROM feature_requests WHERE id = $1", request_id)
        await setup.close()
