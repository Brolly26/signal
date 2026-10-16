"""Analysis lifecycle: queueing, running, recovering and reading.

The rule that shapes this module: **an analysis row is only created when
something will actually process it.** With no API key configured, no row is
queued and a request reports `not_requested` — a pending row nothing can
process would be a lie told by the database.

Failure never costs the submission. The request is committed before any model
is called, and a failed analysis is a row with an actionable `error_code` and a
retry endpoint, not a lost request.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

import asyncpg
from asyncpg.exceptions import UniqueViolationError
from fastapi import HTTPException, status

from app.core.config import Settings
from app.repositories import analyses as analyses_repository
from app.schemas.analysis import AnalysisOut, RelatedRequestOut
from app.services.ai.client import LLMClient
from app.services.ai.orchestrator import AnalysisFailed, run_analysis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisJob:
    """A queued analysis, handed to the background task by the route."""

    analysis_id: UUID
    request_id: UUID
    title: str
    description: str


def build_client(settings: Settings) -> LLMClient | None:
    """The real provider adapter, or None when no key is configured.

    Imported lazily so the SDK is never loaded — and never required — by a test
    run or by an instance with no key.
    """
    if not settings.anthropic_api_key:
        return None

    from app.services.ai.anthropic_client import AnthropicLLMClient

    return AnthropicLLMClient(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        effort=settings.llm_effort,
        max_output_tokens=settings.llm_max_output_tokens,
    )


# ----------------------------------------------------------------- queueing ---


async def queue_analysis(
    connection: asyncpg.Connection,
    *,
    request_id: UUID,
    title: str,
    description: str,
    settings: Settings,
) -> AnalysisJob | None:
    """Create the pending row, or None when there is no provider to run it."""
    if not settings.ai_provider_configured:
        return None

    analysis_id = await analyses_repository.create_pending_analysis(connection, request_id)
    return AnalysisJob(
        analysis_id=analysis_id,
        request_id=request_id,
        title=title,
        description=description,
    )


async def request_retry(
    pool: asyncpg.Pool, request_id: UUID, *, settings: Settings
) -> AnalysisJob:
    """Queue a fresh analysis for a request, releasing an interrupted one first.

    Raises 503 with no provider configured, 404 if the request is gone, and 409
    while an analysis is genuinely still running.
    """
    if not settings.ai_provider_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No AI provider is configured, so analysis cannot run.",
        )

    async with pool.acquire() as connection:
        request = await connection.fetchrow(
            "SELECT title, description FROM feature_requests WHERE id = $1", request_id
        )
        if request is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature request not found.")

        try:
            analysis_id, released = await analyses_repository.retry_analysis(
                connection,
                request_id,
                started_timeout_seconds=settings.stale_analysis_started_seconds,
                never_started_timeout_seconds=settings.stale_analysis_never_started_seconds,
            )
        except UniqueViolationError as error:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "An analysis for this request is already running.",
            ) from error

    if released:
        logger.info("Released %d interrupted analysis(es) for %s", len(released), request_id)

    return AnalysisJob(
        analysis_id=analysis_id,
        request_id=request_id,
        title=request["title"],
        description=request["description"],
    )


# ------------------------------------------------------------------ running ---


async def execute_job(pool: asyncpg.Pool, job: AnalysisJob, *, settings: Settings) -> None:
    """Run a queued analysis to completion. Never raises.

    This is the background task. Every exit path writes a terminal state, so a
    request cannot be left `pending` by anything short of the process dying —
    which the startup sweep then recovers.
    """
    client = build_client(settings)
    if client is None:
        # Only reachable if the key was removed between queueing and running.
        await _record_failure(pool, job.analysis_id, "PROVIDER_ERROR")
        return

    async with pool.acquire() as connection:
        if not await analyses_repository.mark_analysis_started(connection, job.analysis_id):
            # Already failed by the sweep or superseded by a retry.
            logger.info("Analysis %s is no longer pending; abandoning", job.analysis_id)
            return

    try:
        # One connection is held for the whole run because the search tool
        # queries during the model's turns. With a 45s timeout and a pool of 10
        # that is acceptable here; a queue-based worker with its own pool is the
        # production answer, and is recorded as future work.
        async with pool.acquire() as connection:
            run = await run_analysis(
                client=client,
                connection=connection,
                request_id=job.request_id,
                title=job.title,
                description=job.description,
                max_iterations=settings.llm_max_iterations,
                max_tool_calls=settings.llm_max_tool_calls,
            )
    except AnalysisFailed as error:
        logger.info("Analysis %s failed: %s", job.analysis_id, error.error_code)
        await _record_failure(
            pool, job.analysis_id, error.error_code, model_name=client.model_name
        )
        return
    except Exception:
        logger.exception("Analysis %s raised unexpectedly", job.analysis_id)
        await _record_failure(
            pool, job.analysis_id, "UNEXPECTED", model_name=client.model_name
        )
        return

    analysis = run.analysis
    async with pool.acquire() as connection:
        stored = await analyses_repository.complete_analysis(
            connection,
            job.analysis_id,
            classification=analysis.classification,
            recommendation=analysis.recommendation,
            underlying_need=analysis.underlying_need,
            problem_summary=analysis.problem_summary,
            suggested_theme=analysis.suggested_theme,
            reasoning=analysis.reasoning,
            model_name=run.model_name,
            retrieval_mode=run.retrieval_mode,
            tool_calls_count=run.tool_calls_count,
            fabricated_refs_count=analysis.fabricated_refs_count,
            latency_ms=run.latency_ms,
            candidates=[
                (candidate.request_id, candidate.relationship_type, candidate.explanation)
                for candidate in analysis.candidates
            ],
        )

    if not stored:
        logger.info("Analysis %s was resolved elsewhere; result discarded", job.analysis_id)


async def _record_failure(
    pool: asyncpg.Pool,
    analysis_id: UUID,
    error_code: str,
    *,
    model_name: str | None = None,
) -> None:
    try:
        async with pool.acquire() as connection:
            await analyses_repository.fail_analysis(
                connection, analysis_id, error_code, model_name=model_name
            )
    except Exception:
        # Nothing further can be done; the startup sweep will recover the row.
        logger.exception("Could not record failure for analysis %s", analysis_id)


# ---------------------------------------------------------------- recovering ---


async def release_interrupted_analyses(pool: asyncpg.Pool, *, settings: Settings) -> int:
    """Startup sweep: fail analyses left pending by a previous process."""
    async with pool.acquire() as connection:
        released = await analyses_repository.release_stale_analyses(
            connection,
            started_timeout_seconds=settings.stale_analysis_started_seconds,
            never_started_timeout_seconds=settings.stale_analysis_never_started_seconds,
        )
    if released:
        logger.warning("Released %d interrupted analysis(es) at startup", len(released))
    return len(released)


# ------------------------------------------------------------------ reading ---


async def get_latest_for_request(
    pool: asyncpg.Pool, request_id: UUID
) -> AnalysisOut | None:
    async with pool.acquire() as connection:
        analysis_id = await analyses_repository.get_latest_analysis_id(connection, request_id)
        if analysis_id is None:
            return None
        return await _load(connection, analysis_id)


async def get_analysis(pool: asyncpg.Pool, analysis_id: UUID) -> AnalysisOut:
    async with pool.acquire() as connection:
        analysis = await _load(connection, analysis_id)
    if analysis is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found.")
    return analysis


async def _load(connection: asyncpg.Connection, analysis_id: UUID) -> AnalysisOut | None:
    record = await analyses_repository.get_analysis(connection, analysis_id)
    if record is None:
        return None

    candidates = await analyses_repository.get_candidates(connection, analysis_id)
    return AnalysisOut(
        **dict(record),
        related_requests=[
            RelatedRequestOut(**dict(candidate)) for candidate in candidates
        ],
    )
