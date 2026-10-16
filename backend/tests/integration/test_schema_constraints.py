"""Database-level guarantees.

These are integration tests on purpose. The constraints below are the project's
real defences — against double voting, against the model referencing requests
that do not exist, and against an analysis claiming to be complete with no
verdict. Asserting them in Python would only test a copy of the rule.
"""

import pytest
from asyncpg.exceptions import CheckViolationError, ForeignKeyViolationError, UniqueViolationError

pytestmark = pytest.mark.asyncio


# ----------------------------------------------------------- feature requests ---


async def test_valid_request_is_accepted(make_request):
    assert await make_request() is not None


@pytest.mark.parametrize(
    "title, description",
    [
        ("abc", "A description that is comfortably long enough."),
        ("A perfectly fine title", "too short"),
        ("   ", "A description that is comfortably long enough."),
    ],
    ids=["title_too_short", "description_too_short", "title_only_whitespace"],
)
async def test_length_constraints_reject_bad_input(connection, title, description):
    with pytest.raises(CheckViolationError):
        await connection.execute(
            "INSERT INTO feature_requests (title, description) VALUES ($1, $2)",
            title,
            description,
        )


async def test_status_is_restricted_to_known_values(connection, make_request):
    request_id = await make_request()

    with pytest.raises(CheckViolationError):
        await connection.execute(
            "UPDATE feature_requests SET status = 'whatever' WHERE id = $1", request_id
        )


async def test_full_text_search_matches_the_description(connection, make_request):
    await make_request(
        title="Export data to a spreadsheet",
        description="I need to send a weekly sales report to my manager every Friday.",
    )

    found = await connection.fetchval(
        """
        SELECT count(*) FROM feature_requests
         WHERE search_vector @@ plainto_tsquery('english', $1)
        """,
        "weekly sales report",
    )
    assert found >= 1


# ------------------------------------------------------------------- voting ---


async def test_same_voter_cannot_vote_twice(connection, make_request):
    request_id = await make_request()
    await connection.execute(
        "INSERT INTO request_votes (request_id, voter_key) VALUES ($1, $2)",
        request_id,
        "voter-key-aaaaaaaa",
    )

    with pytest.raises(UniqueViolationError):
        await connection.execute(
            "INSERT INTO request_votes (request_id, voter_key) VALUES ($1, $2)",
            request_id,
            "voter-key-aaaaaaaa",
        )


async def test_different_voters_can_support_the_same_request(connection, make_request):
    request_id = await make_request()

    for key in ("voter-key-aaaaaaaa", "voter-key-bbbbbbbb"):
        await connection.execute(
            "INSERT INTO request_votes (request_id, voter_key) VALUES ($1, $2)",
            request_id,
            key,
        )

    total = await connection.fetchval(
        "SELECT count(*) FROM request_votes WHERE request_id = $1", request_id
    )
    assert total == 2


async def test_short_voter_key_is_rejected(connection, make_request):
    request_id = await make_request()

    with pytest.raises(CheckViolationError):
        await connection.execute(
            "INSERT INTO request_votes (request_id, voter_key) VALUES ($1, $2)",
            request_id,
            "short",
        )


# ----------------------------------------------------------------- analyses ---


async def test_only_one_pending_analysis_per_request(connection, make_request):
    request_id = await make_request()
    await connection.execute(
        "INSERT INTO ai_analyses (request_id, status) VALUES ($1, 'pending')", request_id
    )

    with pytest.raises(UniqueViolationError):
        await connection.execute(
            "INSERT INTO ai_analyses (request_id, status) VALUES ($1, 'pending')", request_id
        )


async def test_a_new_analysis_is_allowed_once_the_previous_one_finished(connection, make_request):
    request_id = await make_request()
    first = await connection.fetchval(
        "INSERT INTO ai_analyses (request_id, status) VALUES ($1, 'pending') RETURNING id",
        request_id,
    )
    await connection.execute(
        """
        UPDATE ai_analyses
           SET status = 'failed', error_code = 'TIMEOUT', completed_at = now()
         WHERE id = $1
        """,
        first,
    )

    second = await connection.fetchval(
        "INSERT INTO ai_analyses (request_id, status) VALUES ($1, 'pending') RETURNING id",
        request_id,
    )
    assert second != first


async def test_completed_analysis_must_carry_a_verdict(connection, make_request):
    request_id = await make_request()

    with pytest.raises(CheckViolationError):
        await connection.execute(
            """
            INSERT INTO ai_analyses (request_id, status, completed_at)
            VALUES ($1, 'completed', now())
            """,
            request_id,
        )


async def test_failed_analysis_must_carry_an_error_code(connection, make_request):
    request_id = await make_request()

    with pytest.raises(CheckViolationError):
        await connection.execute(
            "INSERT INTO ai_analyses (request_id, status) VALUES ($1, 'failed')", request_id
        )


@pytest.mark.parametrize(
    "error_code",
    ["TIMEOUT", "PROVIDER_ERROR", "INVALID_OUTPUT", "REFUSAL", "INTERRUPTED",
     "FABRICATED_REFERENCES", "UNEXPECTED"],
)
async def test_every_documented_error_code_is_accepted(connection, make_request, error_code):
    """INTERRUPTED covers recovery; FABRICATED_REFERENCES covers invalid model IDs."""
    request_id = await make_request()

    analysis_id = await connection.fetchval(
        """
        INSERT INTO ai_analyses (request_id, status, error_code, completed_at)
        VALUES ($1, 'failed', $2, now()) RETURNING id
        """,
        request_id,
        error_code,
    )
    assert analysis_id is not None


async def test_unknown_error_code_is_rejected(connection, make_request):
    request_id = await make_request()

    with pytest.raises(CheckViolationError):
        await connection.execute(
            """
            INSERT INTO ai_analyses (request_id, status, error_code)
            VALUES ($1, 'failed', 'SOMETHING_ELSE')
            """,
            request_id,
        )


# ------------------------------------------------- AI-suggested candidates ---


async def test_candidate_must_reference_an_existing_request(connection, make_request):
    """The anti-hallucination guarantee, enforced by a foreign key."""
    request_id = await make_request()
    analysis_id = await connection.fetchval(
        "INSERT INTO ai_analyses (request_id, status) VALUES ($1, 'pending') RETURNING id",
        request_id,
    )

    with pytest.raises(ForeignKeyViolationError):
        await connection.execute(
            """
            INSERT INTO analysis_related_requests
                   (analysis_id, related_request_id, relationship_type, explanation)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', 'DUPLICATE',
                    'A plausible sounding explanation.')
            """,
            analysis_id,
        )


async def test_the_same_candidate_cannot_be_listed_twice(connection, make_request):
    request_id = await make_request()
    other_id = await make_request()
    analysis_id = await connection.fetchval(
        "INSERT INTO ai_analyses (request_id, status) VALUES ($1, 'pending') RETURNING id",
        request_id,
    )

    insert = """
        INSERT INTO analysis_related_requests
               (analysis_id, related_request_id, relationship_type, explanation)
        VALUES ($1, $2, 'RELATED', 'Both describe recurring report delivery.')
    """
    await connection.execute(insert, analysis_id, other_id)

    with pytest.raises(UniqueViolationError):
        await connection.execute(insert, analysis_id, other_id)


# -------------------------------------------------------------- approved links ---


async def test_link_pair_must_be_normalised(connection, make_request):
    """request_a_id < request_b_id forbids self-links and reversed duplicates."""
    first = await make_request()
    second = await make_request()
    low, high = sorted([first, second], key=str)

    await connection.execute(
        "INSERT INTO request_links (request_a_id, request_b_id, link_type) VALUES ($1, $2, 'DUPLICATE')",
        low,
        high,
    )

    with pytest.raises(CheckViolationError):
        await connection.execute(
            "INSERT INTO request_links (request_a_id, request_b_id, link_type) VALUES ($1, $2, 'RELATED')",
            high,
            low,
        )


async def test_a_request_cannot_be_linked_to_itself(connection, make_request):
    request_id = await make_request()

    with pytest.raises(CheckViolationError):
        await connection.execute(
            "INSERT INTO request_links (request_a_id, request_b_id, link_type) VALUES ($1, $1, 'DUPLICATE')",
            request_id,
        )


async def test_the_same_pair_cannot_be_linked_twice(connection, make_request):
    first = await make_request()
    second = await make_request()
    low, high = sorted([first, second], key=str)

    insert = "INSERT INTO request_links (request_a_id, request_b_id, link_type) VALUES ($1, $2, 'DUPLICATE')"
    await connection.execute(insert, low, high)

    with pytest.raises(UniqueViolationError):
        await connection.execute(insert, low, high)
