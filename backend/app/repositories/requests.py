"""Feature request persistence.

`analysis_state` is derived from the most recent analysis row via a lateral
join, rather than denormalised onto `feature_requests`. One source of truth: a
request with no analysis row reports `not_requested`.
"""

import asyncpg

_RETURNED_COLUMNS = """
    r.id, r.title, r.description, r.requester_label, r.status, r.created_at,
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

_LIST_SQL = f"""
SELECT {_RETURNED_COLUMNS}
  FROM feature_requests AS r
  {_LATEST_ANALYSIS_JOIN}
 ORDER BY r.created_at DESC, r.id DESC
 LIMIT $1 OFFSET $2
"""

_COUNT_SQL = "SELECT count(*) FROM feature_requests"


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
) -> list[asyncpg.Record]:
    return list(await connection.fetch(_LIST_SQL, limit, offset))


async def count_requests(connection: asyncpg.Connection) -> int:
    return await connection.fetchval(_COUNT_SQL)
