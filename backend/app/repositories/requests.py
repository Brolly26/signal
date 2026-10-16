"""Feature request persistence.

`analysis_state` is derived from the most recent analysis row via a lateral
join, and `vote_count` from a scalar subquery, rather than denormalised onto
`feature_requests`. One source of truth: a request with no analysis row reports
`not_requested`, and a request with no votes counts zero.
"""

from uuid import UUID

import asyncpg

_RETURNED_COLUMNS = """
    r.id, r.title, r.description, r.requester_label, r.status, r.created_at,
    (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS vote_count,
    coalesce(latest_analysis.status, 'not_requested') AS analysis_state
"""

_LATEST_ANALYSIS_JOIN = """
    LEFT JOIN LATERAL (
        SELECT status
          FROM ai_analyses
         WHERE request_id = r.id
         ORDER BY created_at DESC, id DESC
         LIMIT 1
    ) AS latest_analysis ON true
"""

# Full-text search first, then a plain substring match as a fallback.
#
# The tsvector index handles word-level and stemmed matches ("reports" finds
# "report"), but plainto_tsquery does not match partial words, so a search for
# "rep" would find nothing. The substring arms cover that at the cost of a
# sequential scan; at this scale that is the right trade, and the alternative
# (a trigram index) is noted in docs/decisions.md.
#
# The list and count queries bind the search term at different positions, so the
# placeholder number is a parameter of the builder. An earlier version produced
# the count query with `_SEARCH_PREDICATE.replace("$3", "$1")`, which worked but
# would silently corrupt the query the moment the predicate gained a `$30` or a
# second meaning for `$3`.
def _search_predicate(placeholder: int) -> str:
    param = f"${placeholder}"
    return f"""
    ({param}::text IS NULL
     OR r.search_vector @@ plainto_tsquery('english', {param})
     OR strpos(lower(r.title), lower({param})) > 0
     OR strpos(lower(r.description), lower({param})) > 0)
"""

# Fixed, hand-written ORDER BY clauses selected by a validated enum. Nothing
# from the request is ever interpolated into SQL.
_ORDER_BY_CLAUSES = {
    "recent": "r.created_at DESC, r.id DESC",
    "top": "vote_count DESC, r.created_at DESC, r.id DESC",
}

_INSERT_SQL = f"""
WITH inserted AS (
    INSERT INTO feature_requests (title, description, requester_label)
    VALUES ($1, $2, $3)
    RETURNING id, title, description, requester_label, status, created_at
)
SELECT {_RETURNED_COLUMNS}
  FROM inserted AS r
  {_LATEST_ANALYSIS_JOIN}
"""

_SELECT_ONE_SQL = f"""
SELECT {_RETURNED_COLUMNS}
  FROM feature_requests AS r
  {_LATEST_ANALYSIS_JOIN}
 WHERE r.id = $1
"""

_COUNT_SQL = f"""
SELECT count(*)
  FROM feature_requests AS r
 WHERE {_search_predicate(1)}
"""


def _list_sql(sort: str) -> str:
    return f"""
SELECT {_RETURNED_COLUMNS}
  FROM feature_requests AS r
  {_LATEST_ANALYSIS_JOIN}
 WHERE {_search_predicate(3)}
 ORDER BY {_ORDER_BY_CLAUSES[sort]}
 LIMIT $1 OFFSET $2
"""


async def insert_request(
    connection: asyncpg.Connection,
    *,
    title: str,
    description: str,
    requester_label: str,
) -> asyncpg.Record:
    return await connection.fetchrow(_INSERT_SQL, title, description, requester_label)


async def list_requests(
    connection: asyncpg.Connection,
    *,
    limit: int,
    offset: int,
    query: str | None,
    sort: str,
) -> list[asyncpg.Record]:
    return list(await connection.fetch(_list_sql(sort), limit, offset, query))


async def count_requests(connection: asyncpg.Connection, *, query: str | None) -> int:
    return await connection.fetchval(_COUNT_SQL, query)


async def get_request(
    connection: asyncpg.Connection, request_id: UUID
) -> asyncpg.Record | None:
    return await connection.fetchrow(_SELECT_ONE_SQL, request_id)
