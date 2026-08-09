import os
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.auth import _create_signed_token, ACCESS_TOKEN_PURPOSE


RUN_ID = os.environ.get("ACCEPTANCE_RUN_ID", "d122")


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _make_expired_token() -> str:
    """Create an access token that is already expired."""
    return _create_signed_token(
        "00000000-0000-0000-0000-000000000000",
        purpose=ACCESS_TOKEN_PURPOSE,
        expires_in=timedelta(minutes=-60),
        extra_claims={"sid": "00000000-0000-0000-0000-000000000000"},
    )


# ─── AC1.1: freshly registered caller → count 0 ──────────────────────


async def test_count_returns_zero_for_newly_registered_user():
    async with make_client() as client:
        # Register a brand-new user
        register_resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"{RUN_ID}-notif-count-{_uuid.uuid4().hex[:8]}@test.com",
                "password": "correct horse battery",
                "display_name": "NotifCounter",
            },
        )
        assert register_resp.status_code == 200
        token = register_resp.json()["access_token"]

        count_resp = await client.get(
            "/api/notifications/count",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert count_resp.status_code == 200
    assert count_resp.json() == {"count": 0}


# ─── AC2.1: create one goal → count 1 ────────────────────────────────


async def test_count_returns_one_after_creating_single_goal():
    async with make_client() as client:
        # Register
        register_resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"{RUN_ID}-notif-count-goal-{_uuid.uuid4().hex[:8]}@test.com",
                "password": "correct horse battery",
                "display_name": "NotifCounterGoal",
            },
        )
        assert register_resp.status_code == 200
        token = register_resp.json()["access_token"]

        # Baseline: count should be 0
        count_resp = await client.get(
            "/api/notifications/count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert count_resp.status_code == 200
        assert count_resp.json() == {"count": 0}

        # Create one goal
        goal_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": f"{RUN_ID}-notif-count-check-{_uuid.uuid4().hex[:8]}",
                "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "pledge_amount": 500,
                "goal_type": "api_endpoint",
                "criteria": {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "expected_status": 200,
                },
            },
        )
        assert goal_resp.status_code == 201

        # Count should now be 1
        count_resp = await client.get(
            "/api/notifications/count",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert count_resp.status_code == 200
    assert count_resp.json() == {"count": 1}


# ─── AC3.1: no auth header → 401 ─────────────────────────────────────


async def test_count_rejects_unauthenticated_request():
    async with make_client() as client:
        response = await client.get("/api/notifications/count")
    assert response.status_code == 401


# ─── AC3.2: expired token → 401 ──────────────────────────────────────


async def test_count_rejects_expired_token():
    async with make_client() as client:
        token = _make_expired_token()
        response = await client.get(
            "/api/notifications/count",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


# ─── AC3.3: malformed token → 401 ────────────────────────────────────


async def test_count_rejects_malformed_token():
    async with make_client() as client:
        response = await client.get(
            "/api/notifications/count",
            headers={"Authorization": "Bearer not.a.real.jwt.token"},
        )
    assert response.status_code == 401