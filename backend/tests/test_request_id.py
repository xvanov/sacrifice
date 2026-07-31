import uuid

from app.main import app
from httpx import ASGITransport, AsyncClient

# ── helpers ──────────────────────────────────────────────────────────────────


def _is_valid_uuid4(value: str) -> bool:
    try:
        u = uuid.UUID(value)
        return u.version == 4
    except (ValueError, TypeError):
        return False


# ── AC1.1 / AC5.1: /healthz returns X-Request-ID when client sends none ───────


async def test_healthz_includes_generated_request_id():
    """AC1.1 + AC3.1: GET /healthz returns a generated UUIDv4 X-Request-ID."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None, "X-Request-ID header must be present"
    assert _is_valid_uuid4(request_id), (
        f"X-Request-ID must be a valid UUIDv4, got {request_id!r}"
    )


# ── AC2.1 / AC5.2: echo caller-supplied header ────────────────────────────────


async def test_healthz_echoes_caller_request_id():
    """AC2.1: GET /healthz echoes caller-supplied X-Request-ID verbatim."""
    caller_id = "client-supplied-123"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz", headers={"X-Request-ID": caller_id})

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == caller_id


# ── AC4.1 / AC4.2 / AC5.3: 404 still carries X-Request-ID ─────────────────────


async def test_404_includes_request_id():
    """AC4.1 + AC4.2: a 404 response still includes X-Request-ID header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/does-not-exist")

    assert response.status_code == 404
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None, "X-Request-ID header must be present on 404"
    assert _is_valid_uuid4(request_id), (
        f"X-Request-ID on 404 must be a valid UUIDv4, got {request_id!r}"
    )


async def test_404_echoes_caller_request_id():
    """AC4.1 + AC2.1 combined: 404 echoes caller-supplied X-Request-ID."""
    caller_id = "my-custom-req-id-404"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/does-not-exist", headers={"X-Request-ID": caller_id}
        )

    assert response.status_code == 404
    assert response.headers.get("X-Request-ID") == caller_id
