"""Human review rules.

The decision transitions from ADR-008, all inside one transaction so the audit
log and the approved link can never disagree:

| Decision    | Effect on `request_links`                                      |
|-------------|----------------------------------------------------------------|
| `APPROVED`  | insert the normalised pair, updating `link_type` on conflict    |
| `REJECTED`  | delete the link **only if** no analysis still holds an          |
|             | effective `APPROVED` decision for that pair, in either direction|
| `DISMISSED` | no link effect; acknowledges the analysis as a whole            |

Approving never deletes or merges the original requests. It inserts a link and
marks the request `triaged` — a signal, not destruction.
"""

import logging
from uuid import UUID

import asyncpg
from fastapi import HTTPException, status

from app.repositories import analyses as analyses_repository
from app.repositories import decisions as decisions_repository
from app.schemas.decision import (
    DecisionCreate,
    DecisionOut,
    InboxCandidate,
    InboxItem,
    InboxPage,
    RequestLinkOut,
)

logger = logging.getLogger(__name__)


async def record_decision(
    pool: asyncpg.Pool, analysis_id: UUID, payload: DecisionCreate
) -> InboxItem:
    """Append a decision and reconcile the approved link.

    Returns the analysis as it now stands, so the caller sees the result of its
    own write rather than guessing at it.
    """
    if payload.decision == "DISMISSED":
        if payload.related_request_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A dismissal applies to the analysis, so it takes no related request.",
            )
    elif payload.related_request_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Approving or rejecting requires the related request it applies to.",
        )

    async with pool.acquire() as connection:
        analysis = await analyses_repository.get_analysis(connection, analysis_id)
        if analysis is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found.")
        if analysis["status"] != "completed":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Only a completed analysis can be reviewed.",
            )

        request_id = analysis["request_id"]
        related_request_id = payload.related_request_id
        candidate: asyncpg.Record | None = None

        if related_request_id is not None:
            # A decision may only be made about a relationship this analysis
            # actually proposed. Otherwise a client could invent a link that no
            # analysis ever suggested and no evidence supports.
            candidate = await decisions_repository.get_candidate(
                connection, analysis_id, related_request_id
            )
            if candidate is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "This analysis did not propose that related request.",
                )

        async with connection.transaction():
            await decisions_repository.insert_decision(
                connection,
                analysis_id=analysis_id,
                related_request_id=related_request_id,
                decision=payload.decision,
                reviewer_label=payload.reviewer_label,
                reason=payload.reason,
            )

            if related_request_id is not None:
                if payload.decision == "APPROVED":
                    assert candidate is not None  # guarded above
                    await decisions_repository.upsert_link(
                        connection,
                        request_id=request_id,
                        related_request_id=related_request_id,
                        link_type=candidate["relationship_type"],
                        analysis_id=analysis_id,
                    )
                    await decisions_repository.mark_triaged(connection, request_id)
                else:
                    # Only remove the link if nobody still approves this pair.
                    still_approved = await decisions_repository.pair_is_approved(
                        connection, request_id, related_request_id
                    )
                    if not still_approved:
                        await decisions_repository.delete_link(
                            connection, request_id, related_request_id
                        )
            else:
                await decisions_repository.mark_triaged(connection, request_id)

        return await _load_item(connection, analysis_id)


async def get_inbox(pool: asyncpg.Pool, *, limit: int, offset: int) -> InboxPage:
    async with pool.acquire() as connection:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            rows = await decisions_repository.inbox(connection, limit=limit, offset=offset)
            total = await decisions_repository.inbox_count(connection)
            items = [await _item_from_row(connection, row) for row in rows]

    return InboxPage(items=items, total=total, limit=limit, offset=offset)


async def get_item(pool: asyncpg.Pool, analysis_id: UUID) -> InboxItem:
    async with pool.acquire() as connection:
        item = await _load_item(connection, analysis_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found.")
    return item


async def get_history(
    pool: asyncpg.Pool, *, limit: int, offset: int
) -> tuple[list[DecisionOut], int]:
    async with pool.acquire() as connection:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            rows = await decisions_repository.history(connection, limit=limit, offset=offset)
            total = await decisions_repository.history_count(connection)
    return [DecisionOut(**dict(row)) for row in rows], total


async def get_links(pool: asyncpg.Pool, request_id: UUID) -> list[RequestLinkOut]:
    async with pool.acquire() as connection:
        rows = await decisions_repository.links_for_request(connection, request_id)
    return [RequestLinkOut(**dict(row)) for row in rows]


# ------------------------------------------------------------------ loading ---


async def _item_from_row(connection: asyncpg.Connection, row: asyncpg.Record) -> InboxItem:
    candidates = await decisions_repository.inbox_candidates(connection, row["analysis_id"])
    return InboxItem(
        **dict(row),
        candidates=[InboxCandidate(**dict(candidate)) for candidate in candidates],
    )


async def _load_item(
    connection: asyncpg.Connection, analysis_id: UUID
) -> InboxItem | None:
    row = await connection.fetchrow(
        """
        SELECT a.id AS analysis_id, a.request_id, r.title AS request_title,
               r.description AS request_description,
               (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS vote_count,
               a.classification, a.recommendation, a.underlying_need,
               a.problem_summary, a.suggested_theme, a.reasoning,
               a.created_at, a.completed_at
          FROM ai_analyses AS a
          JOIN feature_requests AS r ON r.id = a.request_id
         WHERE a.id = $1
        """,
        analysis_id,
    )
    if row is None or row["classification"] is None:
        return None
    return await _item_from_row(connection, row)
