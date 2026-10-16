"""Reference checking and derivation. No database, no network, no model."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.ai_output import AIAnalysisOutput, RelatedRequestClaim
from app.services.ai.validation import (
    FabricatedReferences,
    derive_classification,
    validate_output,
)
from app.tools.search_requests import SearchHit

ANALYSED = UUID("11111111-1111-1111-1111-111111111111")
KNOWN_A = UUID("22222222-2222-2222-2222-222222222222")
KNOWN_B = UUID("33333333-3333-3333-3333-333333333333")
NEVER_RETRIEVED = UUID("99999999-9999-9999-9999-999999999999")

RETRIEVED = {
    KNOWN_A: SearchHit(id=KNOWN_A, title="Scheduled reports", description="…", vote_count=4),
    KNOWN_B: SearchHit(id=KNOWN_B, title="Email delivery", description="…", vote_count=1),
}


def claim(request_id, relationship_type="DUPLICATE", explanation="Both describe the same need."):
    return RelatedRequestClaim(
        request_id=str(request_id),
        relationship_type=relationship_type,
        explanation=explanation,
    )


def output(**overrides) -> AIAnalysisOutput:
    payload = {
        "underlying_need": "Send a recurring sales report without manual work.",
        "problem_summary": "The customer rebuilds the same report every week by hand.",
        "suggested_theme": "Reporting",
        "reasoning": "The retrieved requests describe the same recurring delivery need.",
        "needs_clarification": False,
        "related_requests": [],
    }
    payload.update(overrides)
    return AIAnalysisOutput(**payload)


def validate(**overrides):
    return validate_output(
        output(**overrides), analysed_request_id=ANALYSED, retrieved=RETRIEVED
    )


# ------------------------------------------------------------- derivation ---


@pytest.mark.parametrize(
    "needs_clarification, types, expected",
    [
        (False, [], "DISTINCT"),
        (False, ["RELATED"], "RELATED"),
        (False, ["DUPLICATE"], "DUPLICATE"),
        (False, ["RELATED", "DUPLICATE"], "DUPLICATE"),
        (True, [], "NEEDS_CLARIFICATION"),
        (True, ["DUPLICATE"], "NEEDS_CLARIFICATION"),
    ],
    ids=[
        "no_candidates",
        "related_only",
        "duplicate_only",
        "duplicate_wins_over_related",
        "clarification_with_nothing",
        "clarification_takes_precedence",
    ],
)
def test_classification_is_derived_from_the_candidates(needs_clarification, types, expected):
    candidates = tuple(
        type("C", (), {"relationship_type": relationship})() for relationship in types
    )

    assert (
        derive_classification(
            needs_clarification=needs_clarification, candidates=candidates
        )
        == expected
    )


@pytest.mark.parametrize(
    "classification_input, expected_recommendation",
    [
        ({"related_requests": [claim(KNOWN_A, "DUPLICATE")]}, "MERGE_CANDIDATE"),
        ({"related_requests": [claim(KNOWN_A, "RELATED")]}, "LINK_AS_RELATED"),
        ({"related_requests": []}, "KEEP_SEPARATE"),
        ({"needs_clarification": True}, "REQUEST_CLARIFICATION"),
    ],
    ids=["duplicate", "related", "distinct", "clarification"],
)
def test_recommendation_follows_the_classification(
    classification_input, expected_recommendation
):
    assert validate(**classification_input).recommendation == expected_recommendation


# --------------------------------------------------------- reference checks ---


def test_a_retrieved_candidate_is_kept_with_its_evidence():
    result = validate(related_requests=[claim(KNOWN_A, "RELATED", "Shared reporting need.")])

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.request_id == KNOWN_A
    # The title and count come from what we retrieved, never from the model.
    assert candidate.title == "Scheduled reports"
    assert candidate.vote_count == 4
    assert candidate.explanation == "Shared reporting need."
    assert result.fabricated_refs_count == 0


def test_an_id_that_was_never_retrieved_is_discarded_and_counted():
    result = validate(
        related_requests=[claim(KNOWN_A), claim(NEVER_RETRIEVED)]
    )

    assert [c.request_id for c in result.candidates] == [KNOWN_A]
    assert result.fabricated_refs_count == 1
    # The surviving candidate still drives the verdict.
    assert result.classification == "DUPLICATE"


def test_an_unparseable_id_is_discarded_and_counted():
    result = validate(related_requests=[claim(KNOWN_A), claim("not-a-uuid")])

    assert len(result.candidates) == 1
    assert result.fabricated_refs_count == 1


def test_the_analysed_request_cannot_be_its_own_candidate():
    result = validate(related_requests=[claim(KNOWN_A), claim(ANALYSED)])

    assert [c.request_id for c in result.candidates] == [KNOWN_A]
    assert result.fabricated_refs_count == 1


def test_the_same_candidate_twice_is_deduplicated_not_counted_as_fabricated():
    """The first claim wins; a second row would violate the unique constraint."""
    result = validate(
        related_requests=[
            claim(KNOWN_A, "DUPLICATE", "First explanation given."),
            claim(KNOWN_A, "RELATED", "Second explanation given."),
        ]
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].relationship_type == "DUPLICATE"
    assert result.candidates[0].explanation == "First explanation given."
    assert result.fabricated_refs_count == 0


def test_citing_only_unverifiable_ids_is_a_failure_not_a_distinct_verdict():
    """ADR-007: "nothing related" and "references you cannot verify" differ."""
    with pytest.raises(FabricatedReferences):
        validate(related_requests=[claim(NEVER_RETRIEVED), claim("garbage")])


def test_citing_nothing_is_a_valid_distinct_verdict():
    result = validate(related_requests=[])

    assert result.classification == "DISTINCT"
    assert result.candidates == ()
    assert result.fabricated_refs_count == 0


def test_unverifiable_references_fail_even_when_clarification_is_requested():
    """An output whose every reference is invented is unreliable as a whole."""
    with pytest.raises(FabricatedReferences):
        validate(needs_clarification=True, related_requests=[claim(NEVER_RETRIEVED)])


def test_candidates_keep_their_individual_relationship_types():
    result = validate(
        related_requests=[claim(KNOWN_A, "DUPLICATE"), claim(KNOWN_B, "RELATED")]
    )

    by_id = {c.request_id: c.relationship_type for c in result.candidates}
    assert by_id == {KNOWN_A: "DUPLICATE", KNOWN_B: "RELATED"}
    # The aggregate is the strongest relationship present.
    assert result.classification == "DUPLICATE"


# ----------------------------------------------------- the output contract ---


def test_the_model_cannot_set_fields_it_does_not_own():
    """classification and recommendation are derived, so they are rejected."""
    with pytest.raises(ValidationError):
        AIAnalysisOutput(
            underlying_need="A need stated clearly.",
            problem_summary="A summary stated clearly.",
            suggested_theme="Reporting",
            reasoning="Because of the retrieved requests.",
            needs_clarification=False,
            classification="DUPLICATE",
        )


@pytest.mark.parametrize(
    "field, value",
    [
        ("underlying_need", "short"),
        ("problem_summary", "short"),
        ("suggested_theme", "ab"),
        ("reasoning", "short"),
    ],
)
def test_too_short_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        output(**{field: value})


def test_more_than_eight_candidates_is_rejected():
    with pytest.raises(ValidationError):
        output(related_requests=[claim(uuid4()) for _ in range(9)])


def test_an_explanation_is_mandatory_for_every_claim():
    """A relationship without evidence is not usable by a reviewer."""
    with pytest.raises(ValidationError):
        RelatedRequestClaim(
            request_id=str(KNOWN_A), relationship_type="DUPLICATE", explanation="too short"
        )


def test_an_unknown_relationship_type_is_rejected():
    with pytest.raises(ValidationError):
        RelatedRequestClaim(
            request_id=str(KNOWN_A),
            relationship_type="MAYBE_RELATED",
            explanation="A plausible explanation here.",
        )
