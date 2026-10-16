"""The model's output contract — untrusted.

This module is deliberately separate from `analysis.py`. What arrives here is a
claim made by a language model: it may reference requests that do not exist,
contradict itself, or be shaped by text a user wrote. Nothing in this file is
trusted until `services/ai/validation.py` has checked it against data we
retrieved ourselves.

Two fields the model is NOT asked for, because asking would create a
contradiction the backend would have to resolve anyway (ADR-006):

  * `classification` — derived from the validated candidate set
  * `recommendation`  — a pure mapping from the classification

`request_id` is typed as `str`, not `UUID`. The JSON schema sent to the
provider stays free of `format` keywords, and the id is parsed and checked
against an allowlist during validation, where a bad value is a finding rather
than a parse error.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_RELATED_REQUESTS = 8

RelationshipType = Literal["DUPLICATE", "RELATED"]


class RelatedRequestClaim(BaseModel):
    """One claimed relationship. The id is checked before it is believed."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    relationship_type: RelationshipType
    explanation: str = Field(
        min_length=10,
        max_length=2000,
        description="The evidence for this relationship, in one or two sentences.",
    )


class AIAnalysisOutput(BaseModel):
    """What the model must return for an analysis to be considered complete."""

    model_config = ConfigDict(extra="forbid")

    underlying_need: str = Field(
        min_length=10,
        max_length=500,
        description="The customer problem behind the request, not the solution they proposed.",
    )
    problem_summary: str = Field(min_length=10, max_length=1000)
    suggested_theme: str = Field(min_length=3, max_length=60)
    reasoning: str = Field(
        min_length=10,
        max_length=2000,
        description="Why these conclusions follow from the retrieved requests.",
    )
    needs_clarification: bool = Field(
        description="True when the request is too vague to compare against others."
    )
    related_requests: list[RelatedRequestClaim] = Field(
        default_factory=list, max_length=MAX_RELATED_REQUESTS
    )


# Keywords the provider's JSON Schema subset rejects.
#
# Verified against claude-sonnet-5-5 with a real authenticated request on
# 2026-10-16: sending the schema as Pydantic generates it returns
#   HTTP 400 output_config.format.schema:
#   "For 'array' type, property 'maxItems' is not supported"
# `maxItems` comes from the `max_length` bound on `related_requests`. The same
# run confirmed that `$defs`/`$ref` and `additionalProperties: false` ARE
# accepted, so nothing else needs removing.
#
# Nothing is lost by stripping it. The bound still lives on the Pydantic model
# and is still enforced when the response is validated; only the schema used to
# constrain generation is more permissive than our own contract. This is the
# "never trust the model's JSON, validate it again" design doing its job — the
# guarantee is on our side, not delegated to the provider.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({"maxItems"})


def _without_unsupported_keywords(node: object) -> object:
    if isinstance(node, dict):
        return {
            key: _without_unsupported_keywords(value)
            for key, value in node.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYWORDS
        }
    if isinstance(node, list):
        return [_without_unsupported_keywords(item) for item in node]
    return node


def analysis_json_schema() -> dict:
    """The schema handed to the provider to constrain the response.

    Generated from the model above so the two cannot drift, then filtered down
    to the keywords the provider accepts.
    """
    return _without_unsupported_keywords(AIAnalysisOutput.model_json_schema())
