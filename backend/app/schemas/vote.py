"""Vote contracts.

The voter key is a browser-scoped identifier supplied by the client. It stops
the same browser from supporting a request twice, and the `UNIQUE
(request_id, voter_key)` constraint is what actually enforces that. It is not
authentication and makes no claim to stop a determined actor.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Header
from pydantic import BaseModel

VOTER_KEY_MIN_LENGTH = 8
VOTER_KEY_MAX_LENGTH = 64

# A header rather than a body field: DELETE carries no reliable body, and both
# vote endpoints need the same identifier.
VoterKey = Annotated[
    str,
    Header(
        alias="X-Voter-Key",
        min_length=VOTER_KEY_MIN_LENGTH,
        max_length=VOTER_KEY_MAX_LENGTH,
        # Opaque identifier, not free text: keeps arbitrary user input out of a
        # column that is compared and indexed.
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Browser-scoped demo identity. Not an authenticated user.",
    ),
]


class VoteResult(BaseModel):
    """Returned by every vote operation, including the conflict case, so the
    client can correct its local state from the authoritative count."""

    request_id: UUID
    vote_count: int
    has_voted: bool
