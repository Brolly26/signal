"""Human decisions and the approved links they produce.

`review_decisions` is append-only: nothing in this module updates or deletes a
row there. `request_links` is the current state, and it is derived from the most
recent decision per pair.

The pair is unordered. An analysis of A citing B and an analysis of B citing A
describe the same relationship, so both directions are considered when deciding
whether a link should still exist — otherwise rejecting one would quietly remove
a link somebody else approved from the other side.
"""

from uuid import UUID

import asyncpg

_INSERT_DECISION_SQL = """
INSERT INTO review_decisions
       (analysis_id, related_request_id, decision, reviewer_label, reason)
VALUES ($1, $2, $3, $4, $5)
RETURNING id, created_at
"""

# Does any analysis currently hold an effective APPROVED decision for this
# unordered pair? `DISTINCT ON (analysis_id)` collapses the append-only log to
# the latest decision per analysis before the question is asked.
_PAIR_IS_APPROVED_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (d.analysis_id) d.analysis_id, d.decision
      FROM review_decisions AS d
      JOIN ai_analyses AS a ON a.id = d.analysis_id
     WHERE (a.request_id = $1 AND d.related_request_id = $2)
        OR (a.request_id = $2 AND d.related_request_id = $1)
     ORDER BY d.analysis_id, d.created_at DESC, d.id DESC
)
SELECT EXISTS (SELECT 1 FROM latest WHERE decision = 'APPROVED')
"""

# The normalised pair (a < b) is produced here so callers never have to sort.
_UPSERT_LINK_SQL = """
INSERT INTO request_links (request_a_id, request_b_id, link_type, created_from_analysis_id)
VALUES (least($1::uuid, $2::uuid), greatest($1::uuid, $2::uuid), $3, $4)
ON CONFLICT (request_a_id, request_b_id)
DO UPDATE SET link_type = EXCLUDED.link_type
RETURNING id
"""

_DELETE_LINK_SQL = """
DELETE FROM request_links
 WHERE request_a_id = least($1::uuid, $2::uuid)
   AND request_b_id = greatest($1::uuid, $2::uuid)
RETURNING id
"""

_CANDIDATE_SQL = """
SELECT ar.relationship_type, a.request_id
  FROM analysis_related_requests AS ar
  JOIN ai_analyses AS a ON a.id = ar.analysis_id
 WHERE ar.analysis_id = $1 AND ar.related_request_id = $2
"""

_MARK_TRIAGED_SQL = """
UPDATE feature_requests
   SET status = 'triaged', updated_at = now()
 WHERE id = $1 AND status = 'open'
"""

_LINKS_FOR_REQUEST_SQL = """
SELECT CASE WHEN l.request_a_id = $1 THEN l.request_b_id ELSE l.request_a_id END
           AS linked_request_id,
       r.title AS linked_request_title,
       l.link_type,
       l.created_at
  FROM request_links AS l
  JOIN feature_requests AS r
    ON r.id = CASE WHEN l.request_a_id = $1 THEN l.request_b_id ELSE l.request_a_id END
 WHERE l.request_a_id = $1 OR l.request_b_id = $1
 ORDER BY l.created_at DESC
"""

_HISTORY_SQL = """
SELECT d.id,
       d.analysis_id,
       a.request_id,
       r.title AS request_title,
       d.related_request_id,
       related.title AS related_request_title,
       d.decision,
       d.reviewer_label,
       d.reason,
       d.created_at
  FROM review_decisions AS d
  JOIN ai_analyses AS a ON a.id = d.analysis_id
  JOIN feature_requests AS r ON r.id = a.request_id
  LEFT JOIN feature_requests AS related ON related.id = d.related_request_id
 ORDER BY d.created_at DESC, d.id DESC
 LIMIT $1 OFFSET $2
"""

_HISTORY_COUNT_SQL = "SELECT count(*) FROM review_decisions"

# An analysis needs review while any candidate has no decision at all, or while
# a NEEDS_CLARIFICATION verdict has not been acknowledged. A changed decision
# does not send an item back to the queue: the history is where that is seen.
_PENDING_REVIEW_PREDICATE = """
    a.status = 'completed'
    AND (
        EXISTS (
            SELECT 1
              FROM analysis_related_requests AS ar
             WHERE ar.analysis_id = a.id
               AND NOT EXISTS (
                   SELECT 1 FROM review_decisions AS d
                    WHERE d.analysis_id = a.id
                      AND d.related_request_id = ar.related_request_id
               )
        )
        OR (
            a.classification = 'NEEDS_CLARIFICATION'
            AND NOT EXISTS (
                SELECT 1 FROM review_decisions AS d
                 WHERE d.analysis_id = a.id AND d.related_request_id IS NULL
            )
        )
    )
"""

_INBOX_SQL = f"""
SELECT a.id AS analysis_id,
       a.request_id,
       r.title AS request_title,
       r.description AS request_description,
       (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS vote_count,
       a.classification,
       a.recommendation,
       a.underlying_need,
       a.problem_summary,
       a.suggested_theme,
       a.reasoning,
       a.created_at,
       a.completed_at
  FROM ai_analyses AS a
  JOIN feature_requests AS r ON r.id = a.request_id
 WHERE {_PENDING_REVIEW_PREDICATE}
 ORDER BY a.completed_at DESC NULLS LAST, a.id DESC
 LIMIT $1 OFFSET $2
"""

_INBOX_COUNT_SQL = f"""
SELECT count(*)
  FROM ai_analyses AS a
 WHERE {_PENDING_REVIEW_PREDICATE}
"""

# Candidates for one analysis, each with its most recent decision.
_INBOX_CANDIDATES_SQL = """
SELECT ar.related_request_id AS request_id,
       r.title,
       ar.relationship_type,
       ar.explanation,
       (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS vote_count,
       (SELECT d.decision
          FROM review_decisions AS d
         WHERE d.analysis_id = ar.analysis_id
           AND d.related_request_id = ar.related_request_id
         ORDER BY d.created_at DESC, d.id DESC
         LIMIT 1) AS decision
  FROM analysis_related_requests AS ar
  JOIN feature_requests AS r ON r.id = ar.related_request_id
 WHERE ar.analysis_id = $1
 ORDER BY ar.relationship_type, r.title
"""


async def insert_decision(
    connection: asyncpg.Connection,
    *,
    analysis_id: UUID,
    related_request_id: UUID | None,
    decision: str,
    reviewer_label: str,
    reason: str | None,
) -> asyncpg.Record:
    return await connection.fetchrow(
        _INSERT_DECISION_SQL, analysis_id, related_request_id, decision, reviewer_label, reason
    )


async def get_candidate(
    connection: asyncpg.Connection, analysis_id: UUID, related_request_id: UUID
) -> asyncpg.Record | None:
    """The candidate a decision refers to, or None if the analysis never cited it."""
    return await connection.fetchrow(_CANDIDATE_SQL, analysis_id, related_request_id)


async def pair_is_approved(
    connection: asyncpg.Connection, request_id: UUID, related_request_id: UUID
) -> bool:
    return await connection.fetchval(_PAIR_IS_APPROVED_SQL, request_id, related_request_id)


async def upsert_link(
    connection: asyncpg.Connection,
    *,
    request_id: UUID,
    related_request_id: UUID,
    link_type: str,
    analysis_id: UUID,
) -> None:
    await connection.execute(
        _UPSERT_LINK_SQL, request_id, related_request_id, link_type, analysis_id
    )


async def delete_link(
    connection: asyncpg.Connection, request_id: UUID, related_request_id: UUID
) -> bool:
    return (
        await connection.fetchval(_DELETE_LINK_SQL, request_id, related_request_id) is not None
    )


async def mark_triaged(connection: asyncpg.Connection, request_id: UUID) -> None:
    """A signal that the request has been looked at. Never destructive."""
    await connection.execute(_MARK_TRIAGED_SQL, request_id)


async def links_for_request(
    connection: asyncpg.Connection, request_id: UUID
) -> list[asyncpg.Record]:
    return list(await connection.fetch(_LINKS_FOR_REQUEST_SQL, request_id))


async def history(
    connection: asyncpg.Connection, *, limit: int, offset: int
) -> list[asyncpg.Record]:
    return list(await connection.fetch(_HISTORY_SQL, limit, offset))


async def history_count(connection: asyncpg.Connection) -> int:
    return await connection.fetchval(_HISTORY_COUNT_SQL)


async def inbox(
    connection: asyncpg.Connection, *, limit: int, offset: int
) -> list[asyncpg.Record]:
    return list(await connection.fetch(_INBOX_SQL, limit, offset))


async def inbox_count(connection: asyncpg.Connection) -> int:
    return await connection.fetchval(_INBOX_COUNT_SQL)


async def inbox_candidates(
    connection: asyncpg.Connection, analysis_id: UUID
) -> list[asyncpg.Record]:
    return list(await connection.fetch(_INBOX_CANDIDATES_SQL, analysis_id))
