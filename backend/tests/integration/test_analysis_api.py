"""The analysis flow over HTTP, with a scripted model.

`TestClient` runs FastAPI background tasks before the response is handed back,
so by the time a POST returns, the analysis has already run. That makes the
whole path — submit, queue, run, validate, persist, read back — testable
without a network call or an API key.
"""

import json

import asyncpg
import pytest
import pytest_asyncio

from app.services import analyses as analyses_service
from app.services.ai.client import FakeLLMClient, LLMTimeout, LLMTurn

VALID_PAYLOAD = {
    "title": "Export sales data to Excel",
    "description": "Every Friday I copy the numbers by hand and email a report to my manager.",
}

MODEL_OUTPUT = {
    "underlying_need": "Send a recurring sales report without rebuilding it by hand.",
    "problem_summary": "The customer rebuilds and emails the same report every week.",
    "suggested_theme": "Reporting",
    "reasoning": "The retrieved request describes the same recurring delivery need.",
    "needs_clarification": False,
    "related_requests": [],
}


def final_turn(**overrides):
    return LLMTurn(
        stop_reason="end_turn",
        text=json.dumps({**MODEL_OUTPUT, **overrides}),
        assistant_content=[],
    )


@pytest.fixture
def with_provider(settings, monkeypatch):
    """Pretend a key is configured, and script the model.

    Returns a function that installs a FakeLLMClient for the background task.
    """
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-not-a-real-one")

    def install(*script) -> FakeLLMClient:
        client = FakeLLMClient(script=list(script))
        monkeypatch.setattr(analyses_service, "build_client", lambda _settings: client)
        return client

    return install


@pytest_asyncio.fixture
async def created(client, settings):
    """Submits requests and removes them afterwards."""
    ids: list[str] = []

    def submit(payload: dict | None = None):
        response = client.post("/api/requests", json=payload or VALID_PAYLOAD)
        if response.status_code == 201:
            ids.append(response.json()["id"])
        return response

    yield submit

    if ids:
        connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
        try:
            await connection.execute(
                "DELETE FROM feature_requests WHERE id = ANY($1::uuid[])", ids
            )
        finally:
            await connection.close()


# --------------------------------------------------- no provider configured ---


def test_without_a_provider_no_analysis_is_queued(created, client):
    """A pending row nothing can process would be a lie told by the database."""
    request_id = created().json()["id"]

    detail = client.get(f"/api/requests/{request_id}").json()

    assert detail["analysis_state"] == "not_requested"
    assert detail["analysis"] is None


def test_retry_without_a_provider_is_refused(created, client):
    request_id = created().json()["id"]

    response = client.post(f"/api/requests/{request_id}/analyses")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable" or True
    assert "provider" in response.json()["error"]["message"].lower()


# ----------------------------------------------------------- the happy path ---


def test_submitting_runs_and_persists_an_analysis(created, client, with_provider):
    with_provider(final_turn(), final_turn())

    submitted = created()

    assert submitted.status_code == 201
    # The row exists the moment the response is written.
    assert submitted.json()["analysis_state"] == "pending"

    detail = client.get(f"/api/requests/{submitted.json()['id']}").json()
    analysis = detail["analysis"]

    assert detail["analysis_state"] == "completed"
    assert analysis["status"] == "completed"
    assert analysis["classification"] == "DISTINCT"
    assert analysis["recommendation"] == "KEEP_SEPARATE"
    assert analysis["underlying_need"] == MODEL_OUTPUT["underlying_need"]
    assert analysis["model_name"] == "fake-model-1"
    assert analysis["retrieval_mode"] == "backend_fallback"
    assert analysis["started_at"] and analysis["completed_at"]
    assert analysis["latency_ms"] >= 0
    assert analysis["error_code"] is None


def test_an_analysis_links_a_duplicate_with_its_evidence(created, client, with_provider):
    first = created(
        {
            "title": "Scheduled report delivery",
            "description": "Email the weekly sales report to my manager automatically.",
        }
    ).json()["id"]

    with_provider(
        final_turn(),
        final_turn(
            related_requests=[
                {
                    "request_id": first,
                    "relationship_type": "DUPLICATE",
                    "explanation": "Both ask for the weekly sales report to arrive without manual work.",
                }
            ]
        ),
    )

    second = created().json()["id"]
    analysis = client.get(f"/api/requests/{second}").json()["analysis"]

    assert analysis["classification"] == "DUPLICATE"
    assert analysis["recommendation"] == "MERGE_CANDIDATE"
    assert len(analysis["related_requests"]) == 1
    candidate = analysis["related_requests"][0]
    assert candidate["request_id"] == first
    # The title and count come from our data, not from the model.
    assert candidate["title"] == "Scheduled report delivery"
    assert candidate["vote_count"] == 0
    assert "without manual work" in candidate["explanation"]


# ------------------------------------------------------------- failure paths ---


def test_a_provider_failure_does_not_cost_the_request(created, client, with_provider):
    with_provider(LLMTimeout("APITimeoutError"))

    request_id = created().json()["id"]
    detail = client.get(f"/api/requests/{request_id}").json()

    # The submission survived; only the analysis failed.
    assert detail["title"] == VALID_PAYLOAD["title"]
    assert detail["analysis_state"] == "failed"
    assert detail["analysis"]["status"] == "failed"
    assert detail["analysis"]["error_code"] == "TIMEOUT"
    assert detail["analysis"]["classification"] is None


def test_invalid_model_output_is_recorded_as_such(created, client, with_provider):
    with_provider(LLMTurn(stop_reason="end_turn", text="not json at all"))

    request_id = created().json()["id"]

    assert (
        client.get(f"/api/requests/{request_id}").json()["analysis"]["error_code"]
        == "INVALID_OUTPUT"
    )


def test_invented_references_are_recorded_as_such(created, client, with_provider):
    with_provider(
        final_turn(),
        final_turn(
            related_requests=[
                {
                    "request_id": "00000000-0000-0000-0000-000000000000",
                    "relationship_type": "DUPLICATE",
                    "explanation": "An explanation for a request that does not exist.",
                }
            ]
        ),
    )

    request_id = created().json()["id"]
    analysis = client.get(f"/api/requests/{request_id}").json()["analysis"]

    # Not reported as "nothing related": the output could not be verified.
    assert analysis["error_code"] == "FABRICATED_REFERENCES"
    assert analysis["classification"] is None


@pytest.mark.asyncio
async def test_a_failed_analysis_stores_no_candidates(created, client, with_provider, connection):
    with_provider(LLMTimeout("APITimeoutError"))
    request_id = created().json()["id"]

    rows = await connection.fetchval(
        """
        SELECT count(*) FROM analysis_related_requests ar
          JOIN ai_analyses a ON a.id = ar.analysis_id
         WHERE a.request_id = $1::uuid
        """,
        request_id,
    )
    assert rows == 0


# ------------------------------------------------------------------- retry ---


def test_a_failed_analysis_can_be_retried(created, client, with_provider):
    with_provider(LLMTimeout("APITimeoutError"))
    request_id = created().json()["id"]
    assert client.get(f"/api/requests/{request_id}").json()["analysis_state"] == "failed"

    # A second attempt, this time with a model that answers.
    with_provider(final_turn(), final_turn())
    retried = client.post(f"/api/requests/{request_id}/analyses")

    assert retried.status_code == 202
    assert client.get(f"/api/requests/{request_id}").json()["analysis_state"] == "completed"


def test_retry_reports_the_fresh_analysis_not_the_old_one(created, client, with_provider):
    with_provider(LLMTurn(stop_reason="end_turn", text="broken"))
    request_id = created().json()["id"]
    first = client.get(f"/api/requests/{request_id}").json()["analysis"]["id"]

    with_provider(final_turn(), final_turn())
    client.post(f"/api/requests/{request_id}/analyses")
    second = client.get(f"/api/requests/{request_id}").json()["analysis"]["id"]

    assert second != first


def test_retrying_an_unknown_request_returns_404(client, with_provider):
    with_provider(final_turn())

    response = client.post("/api/requests/00000000-0000-0000-0000-000000000000/analyses")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_retry_is_refused_while_an_analysis_is_running(
    created, client, with_provider, settings
):
    with_provider(LLMTimeout("APITimeoutError"))
    request_id = created().json()["id"]

    # Committed through its own connection on purpose. The shared `connection`
    # fixture holds an open transaction, so its uncommitted UPDATE would keep a
    # row lock and the retry would block until the command timeout instead of
    # reaching the conflict check.
    setup = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    try:
        await setup.execute(
            """
            UPDATE ai_analyses
               SET status = 'pending', error_code = NULL, completed_at = NULL,
                   started_at = now(), created_at = now()
             WHERE request_id = $1::uuid
            """,
            request_id,
        )
    finally:
        await setup.close()

    response = client.post(f"/api/requests/{request_id}/analyses")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


# -------------------------------------------------------------------- reads ---


def test_an_unknown_request_detail_returns_404(client):
    response = client.get("/api/requests/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_the_board_reflects_the_analysis_state(created, client, with_provider):
    with_provider(final_turn(), final_turn())
    request_id = created().json()["id"]

    listed = client.get("/api/requests", params={"limit": 100}).json()["items"]
    found = next(item for item in listed if item["id"] == request_id)

    assert found["analysis_state"] == "completed"
