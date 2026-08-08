import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    """Shared ASGI client for meta endpoint tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_meta_returns_200(client):
    """AC1.1: GET /api/meta returns status 200."""
    response = await client.get("/api/meta")
    assert response.status_code == 200


async def test_meta_has_service_field(client):
    """AC1.2: GET /api/meta returns a JSON object containing a service field."""
    response = await client.get("/api/meta")
    body = response.json()
    assert "service" in body


async def test_meta_service_is_sacrifice(client):
    """AC1.3: service field is exactly 'sacrifice'."""
    response = await client.get("/api/meta")
    body = response.json()
    assert body["service"] == "sacrifice"


async def test_meta_has_version_field(client):
    """AC2.1: GET /api/meta returns a JSON object containing a version field."""
    response = await client.get("/api/meta")
    body = response.json()
    assert "version" in body


async def test_meta_version_is_string(client):
    """AC2.2: version field is a string."""
    response = await client.get("/api/meta")
    body = response.json()
    assert isinstance(body["version"], str)


async def test_meta_version_is_non_empty(client):
    """AC2.3: version field is a non-empty string."""
    response = await client.get("/api/meta")
    body = response.json()
    assert len(body["version"]) > 0


async def test_meta_unauthenticated_returns_200(client):
    """AC3.1: unauthenticated GET /api/meta without Authorization header returns 200."""
    response = await client.get("/api/meta")
    assert response.status_code == 200


async def test_meta_unauthenticated_returns_full_body(client):
    """AC3.2: unauthenticated GET /api/meta returns the body with service and version."""
    response = await client.get("/api/meta")
    body = response.json()
    assert body["service"] == "sacrifice"
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


async def test_meta_with_authorization_header_returns_same_contract(client):
    """Authorization header does not change the response contract."""
    unauthenticated_response = await client.get("/api/meta")
    authorized_response = await client.get(
        "/api/meta",
        headers={"Authorization": "Bearer some-fake-token"},
    )

    assert unauthenticated_response.status_code == 200
    assert authorized_response.status_code == 200
    assert authorized_response.json() == unauthenticated_response.json()


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
    finally:
        # Restore only our override; the conftest fixture manages its own.
        del app.dependency_overrides[get_db]


async def test_api_health_unchanged(client):
    """Preserve /api/health contract unchanged."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}