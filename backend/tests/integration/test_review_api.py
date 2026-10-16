"""The Decision Inbox: review, link reconciliation and the audit log.

The rules under test are ADR-008's. The two that matter most, because they are
the ones a careless implementation gets wrong:

  * approving never deletes or merges the original requests
  * rejecting removes the link only if nobody still approves that pair, in
    either direction
"""

import asyncpg
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def world(client, settings):
    """A completed analysis of request B citing A as a duplicate and C as related.

    Built directly in the database: this suite is about the review rules, not
    about how the analysis was produced.
    """
    created: list[str] = []
    connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)

    async def new_request(title, description):
        request_id = await connection.fetchval(
            "INSERT INTO feature_requests (title, description) VALUES ($1, $2) RETURNING id",
            title,
            description,
        )
        created.append(request_id)
        return request_id

    try:
        a = await new_request(
            "Scheduled report delivery", "Email the weekly sales report automatically."
        )
        c = await new_request(
            "Report templates", "Let me save a report layout and reuse it next week."
        )
        b = await new_request(
            "Export sales data to Excel", "Every Friday I copy the numbers by hand."
        )

        analysis_id = await connection.fetchval(
            """
            INSERT INTO ai_analyses
                   (request_id, status, classification, recommendation, underlying_need,
                    problem_summary, suggested_theme, reasoning, model_name,
                    retrieval_mode, tool_calls_count, latency_ms, started_at, completed_at)
            VALUES ($1, 'completed', 'DUPLICATE', 'MERGE_CANDIDATE',
                    'Send a recurring sales report without manual work.',
                    'The customer rebuilds the same report weekly.',
                    'Reporting', 'Both describe the same recurring delivery need.',
                    'fake-model-1', 'agentic', 2, 2500, now(), now())
            RETURNING id
            """,
            b,
        )
        for related_id, relationship in ((a, "DUPLICATE"), (c, "RELATED")):
            await connection.execute(
                """
                INSERT INTO analysis_related_requests
                       (analysis_id, related_request_id, relationship_type, explanation)
                VALUES ($1, $2, $3, 'Shared need for the weekly report to arrive automatically.')
                """,
                analysis_id,
                related_id,
                relationship,
            )

        yield {"a": str(a), "b": str(b), "c": str(c), "analysis": str(analysis_id)}
    finally:
        await connection.execute(
            "DELETE FROM feature_requests WHERE id = ANY($1::uuid[])", created
        )
        await connection.close()


def decide(client, analysis_id, **payload):
    return client.post(f"/api/analyses/{analysis_id}/decisions", json=payload)


def links_of(client, request_id):
    return client.get(f"/api/requests/{request_id}").json()["links"]


# ------------------------------------------------------------------- inbox ---


def test_a_completed_analysis_appears_in_the_inbox(client, world):
    items = client.get("/api/inbox", params={"limit": 100}).json()["items"]
    found = next(item for item in items if item["analysis_id"] == world["analysis"])

    assert found["request_title"] == "Export sales data to Excel"
    assert found["classification"] == "DUPLICATE"
    assert found["underlying_need"]
    assert len(found["candidates"]) == 2
    # Every candidate carries the evidence a reviewer needs.
    assert all(candidate["explanation"] for candidate in found["candidates"])
    assert all(candidate["decision"] is None for candidate in found["candidates"])


def test_the_inbox_is_empty_once_every_candidate_is_decided(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["c"])

    items = client.get("/api/inbox", params={"limit": 100}).json()["items"]

    assert world["analysis"] not in [item["analysis_id"] for item in items]


def test_an_analysis_stays_queued_while_one_candidate_is_undecided(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])

    items = client.get("/api/inbox", params={"limit": 100}).json()["items"]
    found = next(item for item in items if item["analysis_id"] == world["analysis"])

    decisions = {c["request_id"]: c["decision"] for c in found["candidates"]}
    assert decisions[world["a"]] == "APPROVED"
    assert decisions[world["c"]] is None


def test_changing_a_decision_does_not_requeue_the_analysis(client, world):
    """The history is where a change is seen, not the queue."""
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["c"])
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["a"])

    items = client.get("/api/inbox", params={"limit": 100}).json()["items"]
    assert world["analysis"] not in [item["analysis_id"] for item in items]


# -------------------------------------------------------------- approving ---


def test_approving_creates_a_link_and_keeps_both_requests(client, world):
    response = decide(
        client, world["analysis"], decision="APPROVED", related_request_id=world["a"]
    )

    assert response.status_code == 201

    links = links_of(client, world["b"])
    assert [link["linked_request_id"] for link in links] == [world["a"]]
    assert links[0]["link_type"] == "DUPLICATE"
    assert links[0]["linked_request_title"] == "Scheduled report delivery"

    # Neither request was deleted or merged.
    assert client.get(f"/api/requests/{world['a']}").status_code == 200
    assert client.get(f"/api/requests/{world['b']}").status_code == 200


def test_the_link_is_visible_from_both_sides(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])

    assert [link["linked_request_id"] for link in links_of(client, world["a"])] == [
        world["b"]
    ]


def test_approving_marks_the_request_triaged(client, world):
    assert client.get(f"/api/requests/{world['b']}").json()["status"] == "open"

    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])

    assert client.get(f"/api/requests/{world['b']}").json()["status"] == "triaged"


def test_approving_twice_is_idempotent(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])

    assert len(links_of(client, world["b"])) == 1


def test_the_link_type_follows_the_candidate(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["c"])

    links = links_of(client, world["b"])
    assert links[0]["link_type"] == "RELATED"


# -------------------------------------------------------------- rejecting ---


def test_rejecting_creates_no_link(client, world):
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["a"])

    assert links_of(client, world["b"]) == []


def test_rejecting_after_approving_removes_the_link(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])
    assert len(links_of(client, world["b"])) == 1

    decide(
        client,
        world["analysis"],
        decision="REJECTED",
        related_request_id=world["a"],
        reason="Different enough to ship separately.",
    )

    assert links_of(client, world["b"]) == []


def test_approving_again_after_a_rejection_restores_the_link(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["a"])
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])

    assert len(links_of(client, world["b"])) == 1


@pytest.mark.asyncio
async def test_a_rejection_cannot_remove_a_link_approved_from_the_other_side(
    client, world, settings
):
    """The bug this prevents: two analyses describe the same pair, and rejecting
    one silently removes a link another reviewer approved."""
    connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    try:
        # A second analysis, this time of A, citing B.
        other_analysis = await connection.fetchval(
            """
            INSERT INTO ai_analyses
                   (request_id, status, classification, recommendation, underlying_need,
                    problem_summary, suggested_theme, reasoning, completed_at)
            VALUES ($1, 'completed', 'DUPLICATE', 'MERGE_CANDIDATE',
                    'Same recurring report need seen from the other request.',
                    'Summary for the second analysis.', 'Reporting',
                    'Reasoning for the second analysis.', now())
            RETURNING id
            """,
            world["a"],
        )
        await connection.execute(
            """
            INSERT INTO analysis_related_requests
                   (analysis_id, related_request_id, relationship_type, explanation)
            VALUES ($1, $2, 'DUPLICATE', 'The same pair, judged from the other direction.')
            """,
            other_analysis,
            world["b"],
        )
    finally:
        await connection.close()

    # Reviewer one approves the pair from A's analysis.
    decide(client, str(other_analysis), decision="APPROVED", related_request_id=world["b"])
    assert len(links_of(client, world["a"])) == 1

    # Reviewer two rejects the same pair from B's analysis.
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["a"])

    # The link survives, because an effective approval still exists.
    assert len(links_of(client, world["a"])) == 1


# -------------------------------------------------------------- dismissing ---


def test_dismissing_acknowledges_the_analysis_without_touching_links(client, world):
    response = decide(client, world["analysis"], decision="DISMISSED")

    assert response.status_code == 201
    assert links_of(client, world["b"]) == []
    assert client.get(f"/api/requests/{world['b']}").json()["status"] == "triaged"


def test_a_dismissal_may_not_name_a_related_request(client, world):
    response = decide(
        client, world["analysis"], decision="DISMISSED", related_request_id=world["a"]
    )

    assert response.status_code == 422


def test_approving_requires_a_related_request(client, world):
    response = decide(client, world["analysis"], decision="APPROVED")

    assert response.status_code == 422


# ------------------------------------------------------------------ guards ---


def test_a_decision_must_be_about_a_proposed_candidate(client, world):
    """Otherwise a client could invent a link no analysis ever suggested."""
    response = decide(
        client,
        world["analysis"],
        decision="APPROVED",
        related_request_id="00000000-0000-0000-0000-000000000000",
    )

    assert response.status_code == 422
    assert "did not propose" in response.json()["error"]["message"]


def test_deciding_on_an_unknown_analysis_returns_404(client):
    response = decide(
        client,
        "00000000-0000-0000-0000-000000000000",
        decision="DISMISSED",
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_pending_analysis_cannot_be_reviewed(client, world, settings):
    connection = await asyncpg.connect(dsn=settings.database_url, statement_cache_size=0)
    try:
        await connection.execute(
            """
            UPDATE ai_analyses SET status = 'pending', classification = NULL,
                   completed_at = NULL WHERE id = $1::uuid
            """,
            world["analysis"],
        )
    finally:
        await connection.close()

    response = decide(
        client, world["analysis"], decision="APPROVED", related_request_id=world["a"]
    )

    assert response.status_code == 409


@pytest.mark.parametrize("decision", ["MAYBE", "approved", "", "DELETE"])
def test_an_unknown_decision_is_rejected(client, world, decision):
    response = decide(
        client, world["analysis"], decision=decision, related_request_id=world["a"]
    )

    assert response.status_code == 422


def test_an_overlong_reason_is_rejected(client, world):
    response = decide(
        client,
        world["analysis"],
        decision="REJECTED",
        related_request_id=world["a"],
        reason="x" * 2001,
    )

    assert response.status_code == 422


# ----------------------------------------------------------------- history ---


def test_every_decision_is_recorded_with_its_reason_and_reviewer(client, world):
    decide(
        client,
        world["analysis"],
        decision="APPROVED",
        related_request_id=world["a"],
        reason="Clearly the same need.",
        reviewer_label="Ana",
    )

    entries = client.get("/api/decisions", params={"limit": 100}).json()["items"]
    entry = next(e for e in entries if e["analysis_id"] == world["analysis"])

    assert entry["decision"] == "APPROVED"
    assert entry["reason"] == "Clearly the same need."
    assert entry["reviewer_label"] == "Ana"
    assert entry["request_title"] == "Export sales data to Excel"
    assert entry["related_request_title"] == "Scheduled report delivery"
    assert entry["created_at"]


def test_the_history_is_append_only(client, world):
    """Changing a decision adds an entry; it never rewrites one."""
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["a"])

    entries = [
        entry
        for entry in client.get("/api/decisions", params={"limit": 100}).json()["items"]
        if entry["analysis_id"] == world["analysis"]
    ]

    assert [entry["decision"] for entry in entries] == ["REJECTED", "APPROVED"]


def test_the_history_is_newest_first(client, world):
    decide(client, world["analysis"], decision="APPROVED", related_request_id=world["a"])
    decide(client, world["analysis"], decision="REJECTED", related_request_id=world["c"])

    entries = client.get("/api/decisions", params={"limit": 100}).json()["items"]
    timestamps = [entry["created_at"] for entry in entries]

    assert timestamps == sorted(timestamps, reverse=True)


def test_the_response_reflects_the_decision_just_made(client, world):
    body = decide(
        client, world["analysis"], decision="APPROVED", related_request_id=world["a"]
    ).json()

    decisions = {c["request_id"]: c["decision"] for c in body["candidates"]}
    assert decisions[world["a"]] == "APPROVED"
