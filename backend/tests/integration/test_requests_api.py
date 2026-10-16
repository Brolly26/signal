"""POST and GET /api/requests against a real database.

These tests commit, because the point is to prove persistence survives the
request that created it. Every row they create is removed afterwards by the
`submit` fixture.
"""

import asyncpg
import pytest
import pytest_asyncio
from asyncpg.exceptions import PostgresError
from fastapi.testclient import TestClient

from app.main import app
from app.services import requests as requests_service

VALID_PAYLOAD = {
    "title": "Export sales data to Excel",
    "description": "Every Friday I copy numbers by hand and email a report to my manager.",
}


@pytest_asyncio.fixture
async def submit(client, settings):
    """POST helper that cleans up every request it creates."""
    created: list[str] = []

    def _submit(payload: dict | None = None):
        response = client.post("/api/requests", json=VALID_PAYLOAD if payload is None else payload)
        if response.status_code == 201:
            created.append(response.json()["id"])
        return response

    yield _submit

    if created:
        connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
        try:
            await connection.execute(
                "DELETE FROM feature_requests WHERE id = ANY($1::uuid[])", created
            )
        finally:
            await connection.close()


@pytest.fixture
def tolerant_client(client) -> TestClient:
    """A client that returns 5xx responses instead of re-raising.

    Depends on `client` so the application lifespan has already created the
    connection pool.
    """
    return TestClient(app, raise_server_exceptions=False)


# ------------------------------------------------------------------- create ---


def test_create_returns_201_with_the_created_resource(submit):
    response = submit()

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == VALID_PAYLOAD["title"]
    assert body["description"] == VALID_PAYLOAD["description"]
    assert body["status"] == "open"
    assert body["requester_label"] == "anonymous"
    assert body["id"] and body["created_at"]


def test_create_accepts_a_requester_label(submit):
    response = submit({**VALID_PAYLOAD, "title": "Scheduled report delivery", "requester_label": "Ana"})

    assert response.status_code == 201
    assert response.json()["requester_label"] == "Ana"


def test_create_normalises_the_submitted_text(submit):
    response = submit(
        {
            "title": "  Weekly   report\temail  ",
            "description": f"  {VALID_PAYLOAD['description']}  ",
        }
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Weekly report email"
    assert body["description"] == VALID_PAYLOAD["description"]


# ------------------------------------------------------------- persistence ---


def test_a_created_request_is_returned_by_a_later_request(submit, client):
    created_id = submit().json()["id"]

    # A completely separate HTTP call, as a page reload would make.
    listed = client.get("/api/requests", params={"limit": 100})

    assert listed.status_code == 200
    assert created_id in [item["id"] for item in listed.json()["items"]]


@pytest.mark.asyncio
async def test_a_created_request_exists_in_postgres(submit, connection):
    created_id = submit().json()["id"]

    stored = await connection.fetchrow(
        "SELECT title, description, status FROM feature_requests WHERE id = $1::uuid", created_id
    )

    assert stored is not None
    assert stored["title"] == VALID_PAYLOAD["title"]
    assert stored["status"] == "open"


# --------------------------------------------------- no AI work is invented ---


def test_a_new_request_reports_no_analysis(submit):
    assert submit().json()["analysis_state"] == "not_requested"


@pytest.mark.asyncio
async def test_creating_a_request_queues_no_analysis_row(submit, connection):
    """The orchestrator does not exist yet, so nothing may be left pending."""
    created_id = submit().json()["id"]

    analyses = await connection.fetchval(
        "SELECT count(*) FROM ai_analyses WHERE request_id = $1::uuid", created_id
    )

    assert analyses == 0


# ------------------------------------------------------------- validation ---


@pytest.mark.parametrize(
    "payload, expected_field",
    [
        ({"title": "abc", "description": VALID_PAYLOAD["description"]}, "title"),
        ({"title": "   ", "description": VALID_PAYLOAD["description"]}, "title"),
        ({"title": "x" * 141, "description": VALID_PAYLOAD["description"]}, "title"),
        ({"title": VALID_PAYLOAD["title"], "description": "too short"}, "description"),
        ({"title": VALID_PAYLOAD["title"], "description": "  "}, "description"),
        ({"title": VALID_PAYLOAD["title"]}, "description"),
        ({"description": VALID_PAYLOAD["description"]}, "title"),
    ],
    ids=[
        "title_too_short",
        "title_blank",
        "title_too_long",
        "description_too_short",
        "description_blank",
        "description_missing",
        "title_missing",
    ],
)
def test_invalid_payloads_are_rejected_with_the_offending_field(submit, payload, expected_field):
    response = submit(payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert expected_field in error["fields"]


def test_unknown_fields_are_rejected(submit):
    response = submit({**VALID_PAYLOAD, "status": "planned"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_a_rejected_request_is_not_persisted(submit, client):
    before = client.get("/api/requests").json()["total"]

    submit({"title": "no", "description": "no"})

    assert client.get("/api/requests").json()["total"] == before


# ------------------------------------------------------------------- list ---


def test_list_returns_a_page_envelope(client):
    response = client.get("/api/requests")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total", "limit", "offset", "query", "sort"}
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["query"] is None
    assert body["sort"] == "recent"
    assert isinstance(body["items"], list)


def test_list_is_ordered_newest_first(submit, client):
    first = submit({**VALID_PAYLOAD, "title": "Older request about reports"}).json()["id"]
    second = submit({**VALID_PAYLOAD, "title": "Newer request about reports"}).json()["id"]

    ids = [item["id"] for item in client.get("/api/requests", params={"limit": 100}).json()["items"]]

    assert ids.index(second) < ids.index(first)


def test_list_honours_limit_and_offset(submit, client):
    for index in range(3):
        submit({**VALID_PAYLOAD, "title": f"Pagination probe number {index}"})

    first_page = client.get("/api/requests", params={"limit": 2, "offset": 0}).json()
    second_page = client.get("/api/requests", params={"limit": 2, "offset": 2}).json()

    assert len(first_page["items"]) == 2
    assert first_page["total"] >= 3
    first_ids = {item["id"] for item in first_page["items"]}
    assert first_ids.isdisjoint({item["id"] for item in second_page["items"]})


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}, {"limit": "many"}],
    ids=["limit_zero", "limit_too_large", "limit_negative", "offset_negative", "limit_not_a_number"],
)
def test_list_rejects_invalid_pagination(client, params):
    response = client.get("/api/requests", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# ------------------------------------------------------------ backend errors ---

SECRET_MARKERS = ("postgresql://", "password", "signal:signal", "Traceback", "asyncpg")


def test_a_database_failure_returns_503_without_leaking(tolerant_client, monkeypatch):
    async def explode(*args, **kwargs):
        raise PostgresError("connection to server failed: postgresql://signal:signal@localhost")

    monkeypatch.setattr(requests_service, "list_requests", explode)

    response = tolerant_client.get("/api/requests")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    for marker in SECRET_MARKERS:
        assert marker not in response.text


def test_an_unexpected_failure_returns_500_without_leaking(tolerant_client, monkeypatch):
    async def explode(*args, **kwargs):
        raise RuntimeError("dsn=postgresql://signal:signal@localhost/signal")

    monkeypatch.setattr(requests_service, "create_request", explode)

    response = tolerant_client.post("/api/requests", json=VALID_PAYLOAD)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    for marker in SECRET_MARKERS:
        assert marker not in response.text
