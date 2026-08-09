from app.main import app
from httpx import ASGITransport, AsyncClient


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ─── AC1.1 / AC1.2: happy-path zero-goal baseline ───


async def test_goal_count_returns_zero_for_newly_registered_user():
    """AC1.2: newly registered user (zero goals) → 200 with {"count": 0}."""
    async with make_client() as client:
        # Register a brand-new user via email.
        register_resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": "count-test@test.com",
                "password": "correct horse battery",
                "display_name": "Counter",
            },
        )
        assert register_resp.status_code == 200
        token = register_resp.json()["access_token"]

        # Hit the goal-count endpoint.
        count_resp = await client.get(
            "/api/goals/count",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert count_resp.status_code == 200
    body = count_resp.json()
    assert body == {"count": 0}
