"""Health endpoint behaviour."""

SECRET_MARKERS = ("sk-ant", "postgresql://", "password", "ANTHROPIC_API_KEY", "DATABASE_URL")


def test_health_reports_ok_and_schema_version(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    # Proves the migration ran and the tracking table is readable.
    assert body["schema_version"] == "0001_init"


def test_health_reports_ai_provider_as_a_boolean_not_a_key(client):
    body = client.get("/api/health").json()

    assert isinstance(body["ai_provider_configured"], bool)


def test_health_does_not_leak_credentials(client):
    raw = client.get("/api/health").text

    for marker in SECRET_MARKERS:
        assert marker not in raw, f"health response leaked {marker!r}"
