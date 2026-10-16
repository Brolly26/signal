"""Checking the model's claims against data we retrieved ourselves.

Two things happen here, and they are the reason the system can be trusted at
all:

1. **Reference checking.** Every `request_id` the model returns must parse, must
   appear in the allowlist of ids a search actually returned during this run,
   and must not be the request being analysed. Anything else is an unverifiable
   reference: discarded and counted.

2. **Derivation.** The request-level `classification` and `recommendation` are
   computed from the surviving candidates, never taken from the model, so the
   headline cannot contradict the evidence (ADR-006).

If the model cited candidates and *none* of them survive, that is a failure —
`FABRICATED_REFERENCES` — and not a confident `DISTINCT` (ADR-007). "I found
nothing related" and "I produced references you cannot verify" are different
events, and collapsing them would hide the second one from the retry path.
"""

from dataclasses import dataclass
from uuid import UUID

from app.schemas.ai_output import AIAnalysisOutput, RelationshipType
from app.schemas.analysis import Classification, Recommendation
from app.tools.search_requests import SearchHit

_RECOMMENDATION_FOR: dict[Classification, Recommendation] = {
    "DUPLICATE": "MERGE_CANDIDATE",
    "RELATED": "LINK_AS_RELATED",
    "DISTINCT": "KEEP_SEPARATE",
    "NEEDS_CLARIFICATION": "REQUEST_CLARIFICATION",
}


class FabricatedReferences(Exception):
    """The model cited candidates and not one of them could be verified."""


@dataclass(frozen=True)
class ValidatedCandidate:
    request_id: UUID
    title: str
    vote_count: int
    relationship_type: RelationshipType
    explanation: str


@dataclass(frozen=True)
class ValidatedAnalysis:
    underlying_need: str
    problem_summary: str
    suggested_theme: str
    reasoning: str
    classification: Classification
    recommendation: Recommendation
    candidates: tuple[ValidatedCandidate, ...]
    # Unparseable ids, ids never returned by a search, and self-references.
    fabricated_refs_count: int


def derive_classification(
    *, needs_clarification: bool, candidates: tuple[ValidatedCandidate, ...]
) -> Classification:
    """The request-level verdict, computed from the validated candidates."""
    if needs_clarification:
        return "NEEDS_CLARIFICATION"
    if any(candidate.relationship_type == "DUPLICATE" for candidate in candidates):
        return "DUPLICATE"
    if any(candidate.relationship_type == "RELATED" for candidate in candidates):
        return "RELATED"
    return "DISTINCT"


def validate_output(
    output: AIAnalysisOutput,
    *,
    analysed_request_id: UUID,
    retrieved: dict[UUID, SearchHit],
) -> ValidatedAnalysis:
    """Turn an untrusted model output into something the API can assert.

    `retrieved` is the allowlist: every hit the search tool returned during this
    run, keyed by id.
    """
    candidates: list[ValidatedCandidate] = []
    seen: set[UUID] = set()
    unverifiable = 0

    for claim in output.related_requests:
        try:
            request_id = UUID(claim.request_id)
        except ValueError:
            unverifiable += 1
            continue

        # A request cannot relate to itself, and the search excludes it anyway,
        # so this can only come from the model inventing it.
        if request_id == analysed_request_id:
            unverifiable += 1
            continue

        hit = retrieved.get(request_id)
        if hit is None:
            unverifiable += 1
            continue

        # The same candidate twice would violate the unique constraint on
        # (analysis_id, related_request_id); the first claim wins.
        if request_id in seen:
            continue
        seen.add(request_id)

        candidates.append(
            ValidatedCandidate(
                request_id=request_id,
                title=hit.title,
                vote_count=hit.vote_count,
                relationship_type=claim.relationship_type,
                explanation=claim.explanation,
            )
        )

    # Checked before the clarification flag: an output whose every reference is
    # unverifiable is unreliable as a whole, whatever else it claims.
    if output.related_requests and not candidates:
        raise FabricatedReferences(
            f"{unverifiable} reference(s) returned, none verifiable"
        )

    classification = derive_classification(
        needs_clarification=output.needs_clarification,
        candidates=tuple(candidates),
    )

    return ValidatedAnalysis(
        underlying_need=output.underlying_need,
        problem_summary=output.problem_summary,
        suggested_theme=output.suggested_theme,
        reasoning=output.reasoning,
        classification=classification,
        recommendation=_RECOMMENDATION_FOR[classification],
        candidates=tuple(candidates),
        fabricated_refs_count=unverifiable,
    )
