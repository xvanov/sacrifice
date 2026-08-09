from app.main import app
from httpx import ASGITransport, AsyncClient


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


EXPECTED_OPTIONS = ["none", "daily", "weekly", "monthly"]


# ─── AC1 / AC2: happy-path, no auth header ───


async def test_recurrence_options_returns_200_with_exact_options_array():
    """AC1.1/AC1.2: no auth header → 200 with exact options body."""
    async with make_client() as client:
        resp = await client.get("/api/goals/recurrence-options")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"options": EXPECTED_OPTIONS}


async def test_recurrence_options_no_auth_header_returns_200():
    """AC2.1: explicit no-header assertion — same body as happy-path."""
    async with make_client() as client:
        resp = await client.get("/api/goals/recurrence-options")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"options": EXPECTED_OPTIONS}


# ─── AC3: garbage Authorization header ───


async def test_recurrence_options_garbage_bearer_token_still_returns_200():
    """AC3.1/AC3.2: garbage Bearer token → 200 with identical options, never 401."""
    async with make_client() as client:
        resp = await client.get(
            "/api/goals/recurrence-options",
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
    body = resp.json()
    assert body == {"options": EXPECTED_OPTIONS}
