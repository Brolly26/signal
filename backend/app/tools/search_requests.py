"""The one tool the model may call.

Read-only by construction: it issues a SELECT and nothing else. The model
cannot create, change, merge or delete anything — those are business operations
that depend on human decisions.

Three limits are enforced here rather than trusted to the model: the result
count, the fields returned, and the exclusion of the request being analysed.
A model that asks for 500 rows gets 8.
"""

from dataclasses import dataclass
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TOOL_NAME = "search_existing_requests"
MAX_RESULTS = 8
DESCRIPTION_PREVIEW_LENGTH = 400

# Candidate retrieval optimises recall; the model supplies precision.
#
# This is why the operator is OR and not AND. `plainto_tsquery` builds an AND
# query, so searching with a sentence — which is exactly what the model does
# when it searches for the need it identified ("send a recurring sales report
# without rebuilding it by hand") — requires every term to be present and
# therefore matches nothing. Relaxing `&` to `|` on the already-sanitised
# tsquery keeps the stemming, stopword handling and escaping that
# `plainto_tsquery` provides while asking for any shared term, and `ts_rank`
# plus the row limit keep the result set small and ordered by relevance.
#
# The board's own search deliberately keeps AND semantics: a person typing two
# words wants both. These are different jobs (see docs/decisions.md ADR-017).
#
# The analysed request is always excluded, so the model cannot "find" itself
# and call it a duplicate.
_SEARCH_SQL = f"""
WITH search AS (
    SELECT replace(plainto_tsquery('english', $2)::text, '&', '|')::tsquery AS any_term
)
SELECT r.id,
       r.title,
       left(r.description, {DESCRIPTION_PREVIEW_LENGTH}) AS description,
       (SELECT count(*) FROM request_votes v WHERE v.request_id = r.id) AS vote_count
  FROM feature_requests AS r, search
 WHERE r.id <> $1
   AND (
         (search.any_term <> ''::tsquery AND r.search_vector @@ search.any_term)
         OR strpos(lower(r.title), lower($2)) > 0
         OR strpos(lower(r.description), lower($2)) > 0
       )
 ORDER BY ts_rank(r.search_vector, search.any_term) DESC,
          r.created_at DESC
 LIMIT $3
"""


class SearchArguments(BaseModel):
    """Arguments the model supplies. Validated before a query is issued."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(ge=1, le=MAX_RESULTS)


@dataclass(frozen=True)
class SearchHit:
    id: UUID
    title: str
    description: str
    vote_count: int

    def for_model(self) -> dict:
        """Only the fields needed to judge similarity."""
        return {
            "request_id": str(self.id),
            "title": self.title,
            "description": self.description,
            "vote_count": self.vote_count,
        }


def tool_definition() -> dict:
    """The provider-facing tool schema.

    `strict` keeps the arguments schema-valid. Forced tool choice is not
    available on current models (ADR-005), so the description carries the
    instruction to call it.
    """
    return {
        "name": TOOL_NAME,
        "description": (
            "Search existing feature requests by keywords and return the closest "
            "matches. Call this before drawing any conclusion. Search for the "
            "underlying need you identified, not only the literal words of the "
            "request, and search again with different wording if the first "
            "results look incomplete."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing the need to search for.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum results to return (1-{MAX_RESULTS}).",
                },
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
    }


class InvalidToolArguments(Exception):
    """The model called the tool with arguments that failed validation."""


async def execute(
    connection: asyncpg.Connection,
    *,
    analysed_request_id: UUID,
    raw_arguments: dict,
) -> list[SearchHit]:
    """Validate the arguments, then run the search."""
    try:
        arguments = SearchArguments.model_validate(raw_arguments)
    except ValidationError as error:
        raise InvalidToolArguments(str(error)) from error

    rows = await connection.fetch(
        _SEARCH_SQL,
        analysed_request_id,
        arguments.query,
        min(arguments.limit, MAX_RESULTS),
    )
    return [
        SearchHit(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            vote_count=row["vote_count"],
        )
        for row in rows
    ]
