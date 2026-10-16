"""Support (vote) endpoints against a real database."""

import asyncpg
import pytest
import pytest_asyncio

VALID_PAYLOAD = {
    "title": "Scheduled report delivery",
    "description": "I would like the weekly report to be emailed automatically every Friday.",
}

VOTER_A = "voter-aaaaaaaa"
VOTER_B = "voter-bbbbbbbb"
MISSING_REQUEST = "00000000-0000-0000-0000-000000000000"


@pytest_asyncio.fixture
async def request_id(client, settings):
    """A persisted request, removed afterwards along with its votes."""
    response = client.post("/api/requests", json=VALID_PAYLOAD)
    assert response.status_code == 201
    created_id = response.json()["id"]

    yield created_id

    connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    try:
        # ON DELETE CASCADE removes the votes with the request.
        await connection.execute("DELETE FROM feature_requests WHERE id = $1::uuid", created_id)
    finally:
        await connection.close()


def support(client, request_id: str, voter: str = VOTER_A):
    return client.post(f"/api/requests/{request_id}/votes", headers={"X-Voter-Key": voter})


def withdraw(client, request_id: str, voter: str = VOTER_A):
    return client.delete(f"/api/requests/{request_id}/votes", headers={"X-Voter-Key": voter})


# ----------------------------------------------------------------- support ---


def test_supporting_a_request_returns_201_and_the_count(client, request_id):
    response = support(client, request_id)

    assert response.status_code == 201
    body = response.json()
    assert body["request_id"] == request_id
    assert body["vote_count"] == 1
    assert body["has_voted"] is True


def test_supporting_twice_is_idempotent_and_reports_200(client, request_id):
    assert support(client, request_id).status_code == 201

    second = support(client, request_id)

    assert second.status_code == 200
    assert second.json()["vote_count"] == 1
    assert second.json()["has_voted"] is True


@pytest.mark.asyncio
async def test_a_duplicate_vote_creates_no_second_row(client, request_id, connection):
    support(client, request_id)
    support(client, request_id)

    rows = await connection.fetchval(
        "SELECT count(*) FROM request_votes WHERE request_id = $1::uuid AND voter_key = $2",
        request_id,
        VOTER_A,
    )
    assert rows == 1


def test_different_identities_both_count(client, request_id):
    support(client, request_id, VOTER_A)
    second = support(client, request_id, VOTER_B)

    assert second.status_code == 201
    assert second.json()["vote_count"] == 2


@pytest.mark.asyncio
async def test_votes_are_persisted(client, request_id, connection):
    support(client, request_id)

    stored = await connection.fetchval(
        "SELECT voter_key FROM request_votes WHERE request_id = $1::uuid", request_id
    )
    assert stored == VOTER_A


# ---------------------------------------------------------------- withdraw ---


def test_withdrawing_support_decrements_the_count(client, request_id):
    support(client, request_id)

    response = withdraw(client, request_id)

    assert response.status_code == 200
    assert response.json() == {"request_id": request_id, "vote_count": 0, "has_voted": False}


def test_withdrawing_without_having_voted_is_not_an_error(client, request_id):
    response = withdraw(client, request_id)

    assert response.status_code == 200
    assert response.json()["vote_count"] == 0


def test_withdrawing_does_not_affect_other_identities(client, request_id):
    support(client, request_id, VOTER_A)
    support(client, request_id, VOTER_B)

    withdraw(client, request_id, VOTER_A)

    assert support(client, request_id, VOTER_B).json()["vote_count"] == 1


def test_support_can_be_given_again_after_being_withdrawn(client, request_id):
    support(client, request_id)
    withdraw(client, request_id)

    again = support(client, request_id)

    assert again.status_code == 201
    assert again.json()["vote_count"] == 1


# ------------------------------------------------------- the vote count on the board ---


def test_the_board_reports_the_vote_count(client, request_id):
    support(client, request_id, VOTER_A)
    support(client, request_id, VOTER_B)

    listed = client.get("/api/requests", params={"limit": 100}).json()["items"]
    found = next(item for item in listed if item["id"] == request_id)

    assert found["vote_count"] == 2


def test_a_request_with_no_votes_counts_zero(client, request_id):
    listed = client.get("/api/requests", params={"limit": 100}).json()["items"]
    found = next(item for item in listed if item["id"] == request_id)

    assert found["vote_count"] == 0


# ------------------------------------------------------------------ errors ---


def test_supporting_an_unknown_request_returns_404(client):
    response = support(client, MISSING_REQUEST)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_withdrawing_from_an_unknown_request_returns_404(client):
    assert withdraw(client, MISSING_REQUEST).status_code == 404


def test_a_malformed_request_id_is_rejected(client):
    response = client.post(
        "/api/requests/not-a-uuid/votes", headers={"X-Voter-Key": VOTER_A}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_the_voter_key_header_is_required(client, request_id):
    response = client.post(f"/api/requests/{request_id}/votes")

    assert response.status_code == 422
    # The field is named after the header the client actually sends.
    assert "X-Voter-Key" in response.json()["error"]["fields"]


@pytest.mark.parametrize(
    "voter_key",
    ["short", "x" * 65, "has spaces!!", "semi;colon;inject", ""],
    ids=["too_short", "too_long", "illegal_characters", "punctuation", "empty"],
)
def test_an_invalid_voter_key_is_rejected(client, request_id, voter_key):
    response = client.post(
        f"/api/requests/{request_id}/votes", headers={"X-Voter-Key": voter_key}
    )

    assert response.status_code == 422
