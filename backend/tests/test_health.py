from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_healthz_check_requires_no_auth():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_api_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_db_ok_when_db_reachable():
    """AC1.1 / AC4.1: healthy DB returns 200 {"db": "ok"}."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz/db")

    assert response.status_code == 200
    assert response.json() == {"db": "ok"}


async def test_healthz_db_unreachable_when_db_fails():
    """AC2.1 / AC4.2: failing DB returns 503 {"db": "unreachable"}."""
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import get_db

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(side_effect=OSError("connection refused"))

    async def _failing_db():
        yield mock_session

    app.dependency_overrides[get_db] = _failing_db

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz/db")

        assert response.status_code == 503
        assert response.json() == {"db": "unreachable"}
    finally:
        del app.dependency_overrides[get_db]


async def test_healthz_db_requires_no_auth():
    """AC3.3: /healthz/db requires no auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz/db")

    assert response.status_code == 200
    assert response.json() == {"db": "ok"}
