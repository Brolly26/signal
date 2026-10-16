"""The analysis loop.

Bounded on three axes: iterations, tool calls, and the per-request timeout the
client enforces. Nothing here trusts the model — it decides what to search for,
and that is all it decides.

Two retrieval paths, both real and both recorded:

* **agentic** — the model calls the read-only search tool, sees the results, and
  may search again with different wording.
* **backend_fallback** — the model answered without searching. Forced tool
  choice is rejected by current models (ADR-005), so this path cannot be
  prevented, only handled: the backend searches using the need the model just
  extracted and asks once more. That is the deterministic two-call pipeline, and
  `retrieval_mode` records which path produced each analysis so the evaluation
  suite can compare them instead of arguing about them (ADR-004).
"""

import json
import logging
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

import asyncpg
from pydantic import ValidationError

from app.schemas.ai_output import AIAnalysisOutput, analysis_json_schema
from app.schemas.analysis import ErrorCode, RetrievalMode
from app.services.ai.client import (
    LLMClient,
    LLMTimeout,
    LLMUnavailable,
)
from app.services.ai.prompts import (
    SYSTEM_PROMPT,
    build_fallback_context,
    build_user_message,
)
from app.services.ai.validation import (
    FabricatedReferences,
    ValidatedAnalysis,
    validate_output,
)
from app.tools.search_requests import (
    MAX_RESULTS,
    TOOL_NAME,
    InvalidToolArguments,
    SearchHit,
    execute as execute_search,
    tool_definition,
)

logger = logging.getLogger(__name__)


class AnalysisFailed(Exception):
    """An analysis could not be produced. `error_code` says what to do next."""

    def __init__(self, error_code: ErrorCode, detail: str = "") -> None:
        super().__init__(f"{error_code}: {detail}" if detail else error_code)
        self.error_code: ErrorCode = error_code
        self.detail = detail


@dataclass(frozen=True)
class AnalysisRun:
    analysis: ValidatedAnalysis
    model_name: str
    retrieval_mode: RetrievalMode
    tool_calls_count: int
    latency_ms: int


async def run_analysis(
    *,
    client: LLMClient,
    connection: asyncpg.Connection,
    request_id: UUID,
    title: str,
    description: str,
    max_iterations: int,
    max_tool_calls: int,
) -> AnalysisRun:
    started_at = perf_counter()

    tools = [tool_definition()]
    output_schema = analysis_json_schema()
    messages: list[dict] = [
        {"role": "user", "content": build_user_message(title=title, description=description)}
    ]

    # The allowlist: only ids a search actually returned may become candidates.
    retrieved: dict[UUID, SearchHit] = {}
    tool_calls = 0
    fallback_used = False

    for _ in range(max_iterations):
        try:
            turn = await client.create_turn(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                output_schema=output_schema,
            )
        except LLMTimeout as error:
            raise AnalysisFailed("TIMEOUT", str(error)) from error
        except LLMUnavailable as error:
            raise AnalysisFailed("PROVIDER_ERROR", str(error)) from error

        if turn.stop_reason == "refusal":
            raise AnalysisFailed("REFUSAL", turn.refusal_category or "")
        if turn.stop_reason == "max_tokens":
            raise AnalysisFailed("INVALID_OUTPUT", "response truncated")
        if turn.stop_reason == "other":
            raise AnalysisFailed("PROVIDER_ERROR", "unexpected stop reason")

        if turn.stop_reason == "tool_use":
            if not turn.tool_invocations:
                # tool_use with nothing to execute would leave the conversation
                # malformed: the next message would have an empty content list.
                raise AnalysisFailed("INVALID_OUTPUT", "tool_use with no tool calls")

            messages.append({"role": "assistant", "content": turn.assistant_content})
            results, tool_calls = await _run_tools(
                connection=connection,
                request_id=request_id,
                invocations=turn.tool_invocations,
                retrieved=retrieved,
                tool_calls=tool_calls,
                max_tool_calls=max_tool_calls,
            )
            # Every tool_result goes back in a single user message: splitting
            # them teaches the model to stop making parallel calls.
            messages.append({"role": "user", "content": results})
            continue

        # end_turn: the response should be the analysis JSON.
        if not turn.text:
            raise AnalysisFailed("INVALID_OUTPUT", "no text content")

        try:
            output = AIAnalysisOutput.model_validate_json(turn.text)
        except ValidationError as error:
            raise AnalysisFailed(
                "INVALID_OUTPUT", f"{len(error.errors())} schema violation(s)"
            ) from error
        except ValueError as error:  # malformed JSON
            raise AnalysisFailed("INVALID_OUTPUT", "response was not valid JSON") from error

        if tool_calls == 0 and not fallback_used:
            # The model concluded without looking. Retrieve candidates ourselves
            # using the need it just extracted, and ask once more.
            fallback_used = True
            messages.append({"role": "assistant", "content": turn.assistant_content})
            messages.append(
                {
                    "role": "user",
                    "content": await _build_fallback_turn(
                        connection=connection,
                        request_id=request_id,
                        query=output.underlying_need or title,
                        retrieved=retrieved,
                    ),
                }
            )
            continue

        try:
            analysis = validate_output(
                output, analysed_request_id=request_id, retrieved=retrieved
            )
        except FabricatedReferences as error:
            raise AnalysisFailed("FABRICATED_REFERENCES", str(error)) from error

        return AnalysisRun(
            analysis=analysis,
            model_name=client.model_name,
            retrieval_mode="agentic" if tool_calls > 0 else "backend_fallback",
            tool_calls_count=tool_calls,
            latency_ms=int((perf_counter() - started_at) * 1000),
        )

    raise AnalysisFailed("INVALID_OUTPUT", "iteration budget exhausted")


async def _run_tools(
    *,
    connection: asyncpg.Connection,
    request_id: UUID,
    invocations,
    retrieved: dict[UUID, SearchHit],
    tool_calls: int,
    max_tool_calls: int,
) -> tuple[list[dict], int]:
    """Execute the model's tool calls, within budget.

    A rejected or failed call returns an error `tool_result` rather than
    aborting: the model can recover by searching differently, and dropping a
    result would leave the conversation malformed.
    """
    results: list[dict] = []

    for invocation in invocations:
        if invocation.name != TOOL_NAME:
            # The model can only have been offered one tool.
            results.append(_tool_error(invocation.id, "Unknown tool."))
            continue

        if tool_calls >= max_tool_calls:
            results.append(
                _tool_error(
                    invocation.id,
                    "Search budget exhausted. Conclude with the results you have.",
                )
            )
            continue

        tool_calls += 1
        try:
            hits = await execute_search(
                connection,
                analysed_request_id=request_id,
                raw_arguments=invocation.arguments,
            )
        except InvalidToolArguments:
            results.append(
                _tool_error(invocation.id, "Invalid arguments. Check the tool schema.")
            )
            continue

        for hit in hits:
            retrieved[hit.id] = hit

        results.append(
            {
                "type": "tool_result",
                "tool_use_id": invocation.id,
                "content": json.dumps([hit.for_model() for hit in hits]),
            }
        )

    return results, tool_calls


def _tool_error(tool_use_id: str, message: str) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "is_error": True,
        "content": message,
    }


async def _build_fallback_turn(
    *,
    connection: asyncpg.Connection,
    request_id: UUID,
    query: str,
    retrieved: dict[UUID, SearchHit],
) -> str:
    """Retrieve candidates in the backend and describe them to the model."""
    try:
        hits = await execute_search(
            connection,
            analysed_request_id=request_id,
            # Reuses the validated tool path, so the same limits apply.
            raw_arguments={"query": query[:200], "limit": MAX_RESULTS},
        )
    except InvalidToolArguments:
        # Too short a need to search with; proceed with no candidates.
        hits = []

    for hit in hits:
        retrieved[hit.id] = hit

    return build_fallback_context([hit.for_model() for hit in hits])
