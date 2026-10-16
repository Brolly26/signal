"""Request/response contracts for feature requests.

The length bounds here mirror the CHECK constraints in
`supabase/migrations/0001_init.sql` on purpose. Pydantic rejects bad input with
a useful field-level message; the database constraint is the guarantee that
holds even if this layer is bypassed. Both exist deliberately.
"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

TITLE_MIN_LENGTH = 5
TITLE_MAX_LENGTH = 140
DESCRIPTION_MIN_LENGTH = 20
DESCRIPTION_MAX_LENGTH = 4000
LABEL_MAX_LENGTH = 60

# User text is untrusted input. Terminal escape sequences are removed whole:
# stripping the lone ESC byte would leave its payload behind as literal text
# (`\x1b[31m` becoming `[31m`), which is exactly the residue this is meant to
# avoid in anything that later renders or logs the value.
_ANSI_ESCAPE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI sequences, e.g. colour codes
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences, e.g. window title
    r"|\x1b[@-_]"  # remaining two-character escapes
)
# Control characters other than tab and newline.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RUN = re.compile(r"\s+")
_BLANK_LINE_RUN = re.compile(r"\n{3,}")


def _clean_single_line(value: object) -> object:
    """Collapse a value to a single trimmed line. Non-strings pass through."""
    if not isinstance(value, str):
        return value
    without_escapes = _CONTROL_CHARACTERS.sub("", _ANSI_ESCAPE.sub("", value))
    return _WHITESPACE_RUN.sub(" ", without_escapes).strip()


def _clean_multiline(value: object) -> object:
    """Trim a value but keep paragraph breaks. Non-strings pass through."""
    if not isinstance(value, str):
        return value
    normalised_newlines = value.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _CONTROL_CHARACTERS.sub("", _ANSI_ESCAPE.sub("", normalised_newlines))
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
    return _BLANK_LINE_RUN.sub("\n\n", cleaned).strip()


class FeatureRequestCreate(BaseModel):
    """Payload for POST /api/requests."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=TITLE_MIN_LENGTH,
        max_length=TITLE_MAX_LENGTH,
        description="Short summary of the request.",
    )
    description: str = Field(
        min_length=DESCRIPTION_MIN_LENGTH,
        max_length=DESCRIPTION_MAX_LENGTH,
        description="What the user needs and why.",
    )
    requester_label: str = Field(
        default="anonymous",
        min_length=1,
        max_length=LABEL_MAX_LENGTH,
        description="Display name for the demo. Not an authenticated identity.",
    )

    @field_validator("title", "requester_label", mode="before")
    @classmethod
    def _normalise_single_line(cls, value: object) -> object:
        return _clean_single_line(value)

    @field_validator("description", mode="before")
    @classmethod
    def _normalise_description(cls, value: object) -> object:
        return _clean_multiline(value)


# 'not_requested' is the state of a request that has no analysis row at all.
# It is a real derived value, not a placeholder: until the orchestrator exists,
# no analysis is ever queued, so nothing is left pending forever.
AnalysisState = Literal["not_requested", "pending", "completed", "failed"]
RequestStatus = Literal["open", "triaged", "planned", "declined"]


class FeatureRequestOut(BaseModel):
    """A feature request as returned by the API."""

    id: UUID
    title: str
    description: str
    requester_label: str
    status: RequestStatus
    analysis_state: AnalysisState
    created_at: datetime


class FeatureRequestPage(BaseModel):
    """A page of feature requests."""

    items: list[FeatureRequestOut]
    total: int
    limit: int
    offset: int
