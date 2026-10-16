"""Evaluation harness for the analysis pipeline.

Run it:

    python evals/run_eval.py                 # mock mode (default, free)
    python evals/run_eval.py --live          # real model, costs money
    python evals/run_eval.py --live --mode both

What each mode actually measures - this distinction is the point:

* **mock** replays a scripted model answer per case. It measures the *pipeline*:
  reference checking against the allowlist, the derived classification, the
  failure codes, the prompt framing. It says **nothing** about whether a real
  model would answer that way, and the report labels every number as simulated.

* **live** sends the case to the configured model. It measures the model's
  judgement on a labelled set. The set is deliberately small, so the output is a
  regression signal - "did this change make things worse" - and not a claim
  about production accuracy. The report prints the sample size next to every
  figure for that reason.

Each case seeds its fixtures, runs, and is rolled back, so the evaluation never
leaves rows behind and never depends on what is already in the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.services.ai.client import (  # noqa: E402
    FakeLLMClient,
    LLMClient,
    LLMTimeout,
    LLMTurn,
    LLMUnavailable,
    ToolInvocation,
)
from app.services.ai.orchestrator import AnalysisFailed, run_analysis  # noqa: E402
from app.tools.search_requests import MAX_RESULTS, TOOL_NAME  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases.json"


# ----------------------------------------------------------------- outcomes ---


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    expected: str
    actual: str
    notes: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    tool_calls: int | None = None
    retrieval_mode: str | None = None
    fabricated_refs: int = 0
    # Candidate-level outcomes, for precision and recall.
    duplicate_true_positives: int = 0
    duplicate_false_positives: int = 0
    duplicate_false_negatives: int = 0


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)


# -------------------------------------------------------------- mock client ---


def _mock_client(case: dict, fixture_ids: dict[str, str]) -> LLMClient:
    """Build a scripted client for one case.

    The scripted answer always searches first, so the mock exercises the tool
    path rather than the fallback.
    """
    if error := case.get("mock_error"):
        failure = (
            LLMTimeout("scripted timeout")
            if error == "timeout"
            else LLMUnavailable("scripted provider failure")
        )
        return FakeLLMClient(script=[failure], model_name_value="mock-model")

    search_turn = LLMTurn(
        stop_reason="tool_use",
        tool_invocations=(
            ToolInvocation(
                id="eval-search",
                name=TOOL_NAME,
                arguments={
                    "query": case["subject"]["title"],
                    "limit": MAX_RESULTS,
                },
            ),
        ),
        assistant_content=[{"type": "text", "text": "searching"}],
    )

    if raw := case.get("mock_raw_text"):
        final = LLMTurn(stop_reason="end_turn", text=raw, assistant_content=[])
        return FakeLLMClient(script=[search_turn, final], model_name_value="mock-model")

    scripted = case["mock_output"]
    related = []
    for claim in scripted.get("related", []):
        # A claim either names a fixture by key, or carries a literal id when the
        # case is about an invented reference.
        request_id = claim.get("request_id") or fixture_ids[claim["key"]]
        related.append(
            {
                "request_id": request_id,
                "relationship_type": claim["relationship_type"],
                "explanation": claim["explanation"],
            }
        )

    payload = {
        "underlying_need": scripted["underlying_need"],
        "problem_summary": scripted["problem_summary"],
        "suggested_theme": scripted["suggested_theme"],
        "reasoning": scripted["reasoning"],
        "needs_clarification": scripted["needs_clarification"],
        "related_requests": related,
    }
    final = LLMTurn(
        stop_reason="end_turn", text=json.dumps(payload), assistant_content=[]
    )
    return FakeLLMClient(script=[search_turn, final], model_name_value="mock-model")


def _live_client() -> LLMClient:
    settings = get_settings()
    if not settings.anthropic_api_key:
        sys.exit(
            "ANTHROPIC_API_KEY is not set, so --live cannot run. "
            "Add it to backend/.env, or run without --live for mock mode."
        )
    from app.services.ai.anthropic_client import AnthropicLLMClient

    return AnthropicLLMClient(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        effort=settings.llm_effort,
        max_output_tokens=settings.llm_max_output_tokens,
    )


# ------------------------------------------------------------------ scoring ---


def _score(
    case: dict,
    result,
    failure: AnalysisFailed | None,
    prompt_text: str | None,
    key_by_id: dict[str, str],
) -> CaseResult:
    expected = case["expect"]

    if "error_code" in expected:
        actual = failure.error_code if failure else "completed"
        return CaseResult(
            case_id=case["id"],
            passed=actual == expected["error_code"],
            expected=expected["error_code"],
            actual=actual,
        )

    if failure is not None:
        return CaseResult(
            case_id=case["id"],
            passed=False,
            expected=expected["classification"],
            actual=f"failed:{failure.error_code}",
        )

    analysis = result.analysis
    notes: list[str] = []
    passed = analysis.classification == expected["classification"]

    expected_duplicates = set(expected.get("duplicates", []))
    expected_related = set(expected.get("related", []))
    # Candidate ids are mapped back to fixture keys so the scoring reads in
    # labels rather than uuids.
    def keys_for(relationship: str) -> set[str]:
        return {
            key_by_id.get(str(candidate.request_id), f"unknown:{candidate.request_id}")
            for candidate in analysis.candidates
            if candidate.relationship_type == relationship
        }

    actual_duplicates = keys_for("DUPLICATE")
    actual_related = keys_for("RELATED")

    true_positives = len(expected_duplicates & actual_duplicates)
    false_positives = len(actual_duplicates - expected_duplicates)
    false_negatives = len(expected_duplicates - actual_duplicates)

    if false_positives:
        notes.append(f"{false_positives} candidate(s) wrongly called a duplicate")
    if false_negatives:
        notes.append(f"{false_negatives} duplicate(s) missed")
    if actual_related != expected_related:
        notes.append(
            f"related set differed (expected {sorted(expected_related) or 'none'}, "
            f"got {sorted(actual_related) or 'none'})"
        )

    if expected.get("prompt_must_defuse_markers"):
        if prompt_text is None:
            notes.append("prompt was not captured, marker check skipped")
        elif prompt_text.count("</untrusted_request>") != 1:
            passed = False
            notes.append("the injected closing marker was NOT defused")
        else:
            notes.append("injected marker defused")

    if analysis.fabricated_refs_count:
        notes.append(f"{analysis.fabricated_refs_count} unverifiable reference(s) discarded")

    return CaseResult(
        case_id=case["id"],
        passed=passed,
        expected=expected["classification"],
        actual=analysis.classification,
        notes=notes,
        latency_ms=result.latency_ms,
        tool_calls=result.tool_calls_count,
        retrieval_mode=result.retrieval_mode,
        fabricated_refs=analysis.fabricated_refs_count,
        duplicate_true_positives=true_positives,
        duplicate_false_positives=false_positives,
        duplicate_false_negatives=false_negatives,
    )


# ------------------------------------------------------------------ running ---


async def _run_case(connection: asyncpg.Connection, case: dict, *, live: bool) -> CaseResult:
    settings = get_settings()

    # Isolate the case from whatever is already committed in the database.
    #
    # This matters only in live mode, and it matters a lot: the search tool
    # queries the same connection, so without this the real model would find
    # the demo rows as well as the case fixtures, cite them legitimately, and
    # be scored as if it had invented candidates. The delete is inside the
    # per-case transaction that is always rolled back, so nothing is actually
    # removed.
    await connection.execute("DELETE FROM feature_requests")

    fixture_ids: dict[str, str] = {}
    for fixture in case.get("fixtures", []):
        fixture_ids[fixture["key"]] = str(
            await connection.fetchval(
                "INSERT INTO feature_requests (title, description, requester_label)"
                " VALUES ($1, $2, 'eval-fixture') RETURNING id",
                fixture["title"],
                fixture["description"],
            )
        )

    subject_id = await connection.fetchval(
        "INSERT INTO feature_requests (title, description, requester_label)"
        " VALUES ($1, $2, 'eval-subject') RETURNING id",
        case["subject"]["title"],
        case["subject"]["description"],
    )

    client = _live_client() if live else _mock_client(case, fixture_ids)

    result = None
    failure = None
    try:
        result = await run_analysis(
            client=client,
            connection=connection,
            request_id=subject_id,
            title=case["subject"]["title"],
            description=case["subject"]["description"],
            max_iterations=settings.llm_max_iterations,
            max_tool_calls=settings.llm_max_tool_calls,
        )
    except AnalysisFailed as error:
        failure = error

    key_by_id = {value: key for key, value in fixture_ids.items()}

    prompt_text = None
    if isinstance(client, FakeLLMClient) and client.received:
        prompt_text = client.received[0]["messages"][0]["content"]
    elif live:
        from app.services.ai.prompts import build_user_message

        prompt_text = build_user_message(
            title=case["subject"]["title"], description=case["subject"]["description"]
        )

    return _score(case, result, failure, prompt_text, key_by_id)


async def run(live: bool) -> int:
    settings = get_settings()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    if live:
        cases = [case for case in cases if not case.get("mock_only")]

    connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    results: list[CaseResult] = []
    try:
        for case in cases:
            # One rolled-back transaction per case: no state leaks between cases
            # and nothing is left in the database afterwards.
            transaction = connection.transaction()
            await transaction.start()
            try:
                results.append(await _run_case(connection, case, live=live))
            finally:
                await transaction.rollback()
    finally:
        await connection.close()

    _report(results, live=live, model=settings.llm_model if live else "mock-model")
    return 0 if all(result.passed for result in results) else 1


# ------------------------------------------------------------------- report ---


def _report(results: list[CaseResult], *, live: bool, model: str) -> None:
    width = 78
    print("=" * width)
    print("Signal - analysis evaluation")
    print("=" * width)
    if live:
        print(f"MODE: LIVE - real model ({model})")
        print(
            f"  {len(results)} labelled cases. This is a regression signal on a small\n"
            "  labelled set, NOT a measurement of production accuracy."
        )
    else:
        print("MODE: MOCK - model responses are scripted, not generated")
        print(
            "  These numbers measure the PIPELINE (reference checking, derivation,\n"
            "  failure handling, prompt framing). They say nothing about how a real\n"
            "  model would judge these cases. Run with --live for that."
        )
    print("-" * width)

    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"[{mark}] {result.case_id}")
        print(f"       expected {result.expected} | got {result.actual}")
        if result.retrieval_mode:
            print(
                f"       retrieval {result.retrieval_mode} | "
                f"{_fmt(result.tool_calls)} tool call(s) | {_fmt(result.latency_ms)} ms"
            )
        for note in result.notes:
            print(f"       | {note}")

    print("-" * width)

    passed = sum(1 for result in results if result.passed)
    print(f"Cases passed: {passed}/{len(results)}")

    true_positives = sum(r.duplicate_true_positives for r in results)
    false_positives = sum(r.duplicate_false_positives for r in results)
    false_negatives = sum(r.duplicate_false_negatives for r in results)
    proposed = true_positives + false_positives
    actual_duplicates = true_positives + false_negatives

    print()
    print("Duplicate recommendations (candidate level):")
    print(f"  proposed {proposed} | correct {true_positives} | wrong {false_positives}")
    if proposed:
        print(f"  precision {true_positives / proposed:.2f}  (n={proposed})")
    else:
        print("  precision -  (nothing was proposed as a duplicate)")
    if actual_duplicates:
        print(
            f"  recall    {true_positives / actual_duplicates:.2f}  (n={actual_duplicates})"
        )

    fabricated = sum(r.fabricated_refs for r in results)
    print()
    print(f"Unverifiable references discarded: {fabricated}")

    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    if latencies:
        print(
            f"Latency: mean {sum(latencies) // len(latencies)} ms | "
            f"max {max(latencies)} ms  (n={len(latencies)})"
        )

    print()
    print("Classification outcomes (expected -> got):")
    matrix: dict[tuple[str, str], int] = {}
    for result in results:
        matrix[(result.expected, result.actual)] = (
            matrix.get((result.expected, result.actual), 0) + 1
        )
    for (expected, actual), count in sorted(matrix.items()):
        flag = "" if expected == actual else "   <-- mismatch"
        print(f"  {expected:22s} -> {actual:22s} {count}{flag}")

    print("=" * width)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the real model. Requires ANTHROPIC_API_KEY and costs money.",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(run(live=arguments.live)))
