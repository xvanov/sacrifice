from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_meta_returns_200():
    """AC1.1: GET /api/meta returns status 200."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    assert response.status_code == 200


async def test_meta_has_service_field():
    """AC1.2: GET /api/meta returns a JSON object containing a service field."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    body = response.json()
    assert "service" in body


async def test_meta_service_is_sacrifice():
    """AC1.3: service field is exactly 'sacrifice'."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    body = response.json()
    assert body["service"] == "sacrifice"


async def test_meta_has_version_field():
    """AC2.1: GET /api/meta returns a JSON object containing a version field."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    body = response.json()
    assert "version" in body


async def test_meta_version_is_string():
    """AC2.2: version field is a string."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    body = response.json()
    assert isinstance(body["version"], str)


async def test_meta_version_is_non_empty():
    """AC2.3: version field is a non-empty string."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    body = response.json()
    assert len(body["version"]) > 0


async def test_meta_unauthenticated_returns_200():
    """AC3.1: unauthenticated GET /api/meta without Authorization header returns 200."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    assert response.status_code == 200


async def test_meta_unauthenticated_returns_full_body():
    """AC3.2: unauthenticated GET /api/meta returns the body with service and version."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    body = response.json()
    assert body["service"] == "sacrifice"
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


async def test_meta_with_authorization_header_returns_same_contract():
    """Authorization header does not change the response contract."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/meta",
            headers={"Authorization": "Bearer some-fake-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "sacrifice"
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


async def test_meta_no_user_state_required():
    """Endpoint does not require any pre-existing user/account state."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "sacrifice"
    assert body["version"] is not None
    assert len(body["version"]) > 0


async def test_api_health_unchanged():
    """Preserve /api/health contract unchanged."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}