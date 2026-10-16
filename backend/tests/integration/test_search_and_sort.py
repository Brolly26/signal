"""Search and ordering on the board.

Each test seeds its own requests and removes them afterwards, and every
assertion is scoped to the ids it created, so a populated database does not
change the outcome.
"""

import asyncpg
import pytest
import pytest_asyncio

SEEDS = {
    "excel": {
        "title": "Export sales data to Excel",
        "description": "I need to download the numbers as a spreadsheet each week.",
    },
    "scheduled": {
        "title": "Scheduled report delivery",
        "description": "Email the weekly sales report to my manager automatically.",
    },
    "sso": {
        "title": "Single sign-on with Okta",
        "description": "Our security team requires SAML authentication for all tools.",
    },
}


@pytest_asyncio.fixture
async def seeded(client, settings):
    """Insert the seed requests and return {key: id}."""
    ids: dict[str, str] = {}
    for key, payload in SEEDS.items():
        response = client.post("/api/requests", json=payload)
        assert response.status_code == 201, response.text
        ids[key] = response.json()["id"]

    yield ids

    connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    try:
        await connection.execute(
            "DELETE FROM feature_requests WHERE id = ANY($1::uuid[])", list(ids.values())
        )
    finally:
        await connection.close()


def search(client, query: str | None = None, **params) -> dict:
    if query is not None:
        params["q"] = query
    params.setdefault("limit", 100)
    response = client.get("/api/requests", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids_in(page: dict) -> list[str]:
    return [item["id"] for item in page["items"]]


# ----------------------------------------------------------------- search ---


def test_search_matches_the_title(client, seeded):
    found = ids_in(search(client, "Okta"))

    assert seeded["sso"] in found
    assert seeded["excel"] not in found


def test_search_matches_the_description(client, seeded):
    found = ids_in(search(client, "spreadsheet"))

    assert seeded["excel"] in found
    assert seeded["sso"] not in found


def test_search_is_case_insensitive(client, seeded):
    assert seeded["sso"] in ids_in(search(client, "okta"))
    assert seeded["sso"] in ids_in(search(client, "OKTA"))


def test_search_uses_stemming_from_the_text_index(client, seeded):
    """`reports` must find `report`: that is the tsvector doing the work, not a
    substring match."""
    found = ids_in(search(client, "reports"))

    assert seeded["scheduled"] in found


def test_search_matches_a_partial_word(client, seeded):
    """plainto_tsquery alone would miss this; the substring fallback covers it."""
    found = ids_in(search(client, "spreadsh"))

    assert seeded["excel"] in found


def test_search_finds_every_request_sharing_a_term(client, seeded):
    found = ids_in(search(client, "sales"))

    assert seeded["excel"] in found
    assert seeded["scheduled"] in found
    assert seeded["sso"] not in found


def test_a_search_with_no_match_returns_an_empty_page(client, seeded):
    page = search(client, "quantum teleportation")

    assert page["items"] == []
    assert page["total"] == 0


def test_a_blank_search_is_treated_as_no_search(client, seeded):
    page = search(client, "   ")

    assert page["query"] is None
    for request_id in seeded.values():
        assert request_id in ids_in(page)


def test_the_total_reflects_the_search_not_the_whole_table(client, seeded):
    narrow = search(client, "Okta")
    everything = search(client)

    assert narrow["total"] < everything["total"]
    assert narrow["total"] == len(narrow["items"])


def test_search_text_is_not_interpreted_as_sql_or_a_pattern(client, seeded):
    """User input reaches the database only as a bound parameter."""
    for hostile in ["'; DROP TABLE feature_requests; --", "100%", "_____", "\\"]:
        page = search(client, hostile)
        assert isinstance(page["items"], list)

    # The table is still there and still holds the seeds.
    assert seeded["excel"] in ids_in(search(client))


def test_an_overlong_search_is_rejected(client):
    response = client.get("/api/requests", params={"q": "x" * 201})

    assert response.status_code == 422
    assert "q" in response.json()["error"]["fields"]


# ------------------------------------------------------------------- sort ---


def test_recent_is_the_default_order(client, seeded):
    page = search(client)

    assert page["sort"] == "recent"
    found = ids_in(page)
    # Seeded in dict order, so the last one inserted must come first.
    assert found.index(seeded["sso"]) < found.index(seeded["excel"])


def test_top_orders_by_support(client, seeded):
    for voter in ("voter-key-1a", "voter-key-2b", "voter-key-3c"):
        client.post(
            f"/api/requests/{seeded['excel']}/votes", headers={"X-Voter-Key": voter}
        )
    client.post(
        f"/api/requests/{seeded['sso']}/votes", headers={"X-Voter-Key": "voter-key-1a"}
    )

    page = search(client, sort="top")
    found = ids_in(page)

    assert page["sort"] == "top"
    assert found.index(seeded["excel"]) < found.index(seeded["sso"])
    assert found.index(seeded["sso"]) < found.index(seeded["scheduled"])


def test_search_and_sort_combine(client, seeded):
    client.post(
        f"/api/requests/{seeded['scheduled']}/votes", headers={"X-Voter-Key": "voter-key-9z"}
    )

    page = search(client, "sales", sort="top")
    found = ids_in(page)

    assert seeded["sso"] not in found
    assert found.index(seeded["scheduled"]) < found.index(seeded["excel"])


@pytest.mark.parametrize("sort", ["votes", "oldest", "RECENT", ""])
def test_an_unknown_sort_is_rejected(client, sort):
    response = client.get("/api/requests", params={"sort": sort})

    assert response.status_code == 422
    assert "sort" in response.json()["error"]["fields"]


# ------------------------------------------------------------- pagination ---


def test_pagination_applies_to_a_search(client, seeded):
    first = search(client, "sales", limit=1, offset=0)
    second = search(client, "sales", limit=1, offset=1)

    assert len(first["items"]) == 1
    assert len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    # The total is scoped to the search but counts every matching row in the
    # database, not only this test's seeds, so it is asserted as a lower bound.
    assert first["total"] == second["total"] >= 2
