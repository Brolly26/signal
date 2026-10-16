"""Human review contracts.

A decision is an event, not a state: `review_decisions` is append-only, so
changing your mind adds a row rather than editing one. The current state of a
pair is its most recent decision, and the approved link lives in
`request_links` (ADR-008).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ai_output import RelationshipType
from app.schemas.analysis import Classification

Decision = Literal["APPROVED", "REJECTED", "DISMISSED"]


class DecisionCreate(BaseModel):
    """What a reviewer submits."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    # Omitted for DISMISSED, which is about the analysis as a whole.
    related_request_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)
    reviewer_label: str = Field(default="demo-pm", min_length=1, max_length=60)

    @field_validator("reason", "reviewer_label", mode="before")
    @classmethod
    def _trim(cls, value: object) -> object:
        if isinstance(value, str):
            trimmed = value.strip()
            return trimmed or None
        return value

    @field_validator("reviewer_label", mode="after")
    @classmethod
    def _default_reviewer(cls, value: str | None) -> str:
        return value or "demo-pm"


class DecisionOut(BaseModel):
    """One entry in the audit log."""

    id: UUID
    analysis_id: UUID
    request_id: UUID
    request_title: str
    related_request_id: UUID | None
    related_request_title: str | None
    decision: Decision
    reviewer_label: str
    reason: str | None
    created_at: datetime


class RequestLinkOut(BaseModel):
    """A human-approved relationship, as seen from one request."""

    linked_request_id: UUID
    linked_request_title: str
    link_type: RelationshipType
    created_at: datetime


class InboxCandidate(BaseModel):
    """A candidate relationship awaiting a decision, with its evidence."""

    request_id: UUID
    title: str
    relationship_type: RelationshipType
    explanation: str
    vote_count: int
    # The most recent decision for this pair, if one has been made.
    decision: Decision | None = None


class InboxItem(BaseModel):
    """One analysis in the review queue."""

    analysis_id: UUID
    request_id: UUID
    request_title: str
    request_description: str
    vote_count: int
    classification: Classification
    recommendation: str | None
    underlying_need: str | None
    problem_summary: str | None
    suggested_theme: str | None
    reasoning: str | None
    candidates: list[InboxCandidate]
    created_at: datetime
    completed_at: datetime | None


class InboxPage(BaseModel):
    items: list[InboxItem]
    total: int
    limit: int
    offset: int
