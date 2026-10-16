"""The API's analysis contract — what Signal itself asserts.

Everything here has survived validation: every `related_request_id` exists,
came from a search this run actually performed, and is not the analysed request
itself. `classification` and `recommendation` are derived by the backend, never
taken from the model.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.ai_output import RelationshipType

AnalysisStatus = Literal["pending", "completed", "failed"]

Classification = Literal["DUPLICATE", "RELATED", "DISTINCT", "NEEDS_CLARIFICATION"]

Recommendation = Literal[
    "MERGE_CANDIDATE", "LINK_AS_RELATED", "KEEP_SEPARATE", "REQUEST_CLARIFICATION"
]

# Why an analysis failed. Each value is actionable: it tells a reviewer whether
# to retry, wait, or look at the request text.
ErrorCode = Literal[
    "TIMEOUT",
    "PROVIDER_ERROR",
    "INVALID_OUTPUT",
    "REFUSAL",
    "INTERRUPTED",
    "FABRICATED_REFERENCES",
    "UNEXPECTED",
]

# Which retrieval path produced the analysis. Recorded so the evaluation suite
# can compare them rather than argue about them (ADR-004).
RetrievalMode = Literal["agentic", "backend_fallback"]


class RelatedRequestOut(BaseModel):
    """A validated candidate relationship, with the evidence for it."""

    request_id: UUID
    title: str
    relationship_type: RelationshipType
    explanation: str
    vote_count: int


class AnalysisOut(BaseModel):
    """An analysis as returned by the API."""

    id: UUID
    request_id: UUID
    status: AnalysisStatus

    classification: Classification | None = None
    recommendation: Recommendation | None = None
    underlying_need: str | None = None
    problem_summary: str | None = None
    suggested_theme: str | None = None
    reasoning: str | None = None

    related_requests: list[RelatedRequestOut] = []

    # Observability. No prompt text and no user content is stored.
    model_name: str | None = None
    retrieval_mode: RetrievalMode | None = None
    tool_calls_count: int | None = None
    fabricated_refs_count: int = 0
    latency_ms: int | None = None
    error_code: ErrorCode | None = None

    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
