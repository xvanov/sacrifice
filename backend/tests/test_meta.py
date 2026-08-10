from datetime import datetime, timezone

import pytest_asyncio
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client():
    """Shared ASGI client for meta endpoint tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── AC1: existing contract unchanged ──────────────────────────────────────


async def test_meta_returns_200(client):
    """AC1.1: GET /api/meta returns status 200."""
    response = await client.get("/api/meta")
    assert response.status_code == 200


async def test_meta_service_is_sacrifice(client):
    """AC1.2: service field is exactly 'sacrifice'."""
    response = await client.get("/api/meta")
    body = response.json()
    assert body["service"] == "sacrifice"


async def test_meta_version_is_non_empty_string(client):
    """AC1.3: version field is a non-empty string."""
    response = await client.get("/api/meta")
    body = response.json()
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


# ── AC2: server_time present and timezone-aware ISO-8601 ──────────────────


async def test_meta_server_time_is_string(client):
    """AC2.1: server_time key is a string."""
    response = await client.get("/api/meta")
    body = response.json()
    assert "server_time" in body
    assert isinstance(body["server_time"], str)


async def test_meta_server_time_parseable_as_iso8601(client):
    """AC2.2: server_time is parseable by datetime.fromisoformat."""
    response = await client.get("/api/meta")
    body = response.json()
    parsed = datetime.fromisoformat(body["server_time"])
    assert isinstance(parsed, datetime)


async def test_meta_server_time_is_timezone_aware(client):
    """AC2.3: parsed server_time has non-None tzinfo."""
    response = await client.get("/api/meta")
    body = response.json()
    parsed = datetime.fromisoformat(body["server_time"])
    assert parsed.tzinfo is not None


# ── AC3: computed per-request, not crash-once static ──────────────────────


async def test_meta_two_calls_both_return_valid_server_time(client):
    """AC3.1 + AC3.2: two calls both 200 and both have valid server_time."""
    response1 = await client.get("/api/meta")
    response2 = await client.get("/api/meta")

    assert response1.status_code == 200
    body1 = response1.json()
    parsed1 = datetime.fromisoformat(body1["server_time"])
    assert parsed1.tzinfo is not None

    assert response2.status_code == 200
    body2 = response2.json()
    parsed2 = datetime.fromisoformat(body2["server_time"])
    assert parsed2.tzinfo is not None


# ── existing cross-cutting tests (updated for server_time) ────────────────


async def test_meta_missing_auth_returns_same_static_contract(client):
    """Auth token does not change the static fields (service, version)."""
    unauthenticated_response = await client.get("/api/meta")
    authorized_response = await client.get(
        "/api/meta",
        headers={"Authorization": "Bearer some-fake-token"},
    )

    assert unauthenticated_response.status_code == 200
    assert authorized_response.status_code == 200

    unauth_body = unauthenticated_response.json()
    auth_body = authorized_response.json()
    assert unauth_body["service"] == auth_body["service"] == "sacrifice"
    assert isinstance(auth_body["version"], str) and len(auth_body["version"]) > 0
    assert unauth_body["version"] == auth_body["version"]

    # Both must have valid server_time (per-request, may differ).
    for body in (unauth_body, auth_body):
        assert isinstance(body["server_time"], str)
        parsed = datetime.fromisoformat(body["server_time"])
        assert parsed.tzinfo is not None


async def test_meta_no_user_state_required():
    """Endpoint does not require any pre-existing user/account state.

    Proves the endpoint avoids all database access by overriding get_db
    to fail if invoked, then asserting GET /api/meta still succeeds.
    """
    from app.database import get_db

    async def _db_must_not_be_called():
        raise AssertionError("meta endpoint must not touch the database")
        yield  # pragma: no cover — unreachable

    app.dependency_overrides[get_db] = _db_must_not_be_called

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/meta")

        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "sacrifice"
        assert isinstance(body["version"], str)
        assert len(body["version"]) > 0
        assert isinstance(body["server_time"], str)
        parsed = datetime.fromisoformat(body["server_time"])
        assert parsed.tzinfo is not None
    finally:
        # Restore only our override; the conftest fixture manages its own.
        del app.dependency_overrides[get_db]


async def test_api_health_unchanged(client):
    """Preserve /api/health contract unchanged."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
