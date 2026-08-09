import os
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient

from app.main import app

ACCEPTANCE_RUN_ID = os.environ.get("ACCEPTANCE_RUN_ID", "d122")


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _register(client, email, password="longenoughpw"):
    resp = await client.post(
        "/api/auth/email/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"Register failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


def _goal_body(title):
    return {
        "title": title,
        "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "pledge_amount": 500,
        "goal_type": "api_endpoint",
        "criteria": {
            "url": "https://example.com/health",
            "method": "GET",
            "expected_status": 200,
        },
    }


# ─── AC1: fresh account → count 0 ────────────────────────────────────


async def test_count_zero_for_fresh_account():
    async with make_client() as client:
        email = f"{ACCEPTANCE_RUN_ID}-count-fresh@test.com"
        token = await _register(client, email)
        resp = await client.get(
            "/api/notifications/count",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"count": 0}


# ─── AC2: one goal → count 1 ─────────────────────────────────────────


async def test_count_one_after_goal_creation():
    async with make_client() as client:
        email = f"{ACCEPTANCE_RUN_ID}-count-goal@test.com"
        token = await _register(client, email)

        # Baseline: count is 0 before any goal
        resp = await client.get(
            "/api/notifications/count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"count": 0}

        # Create one goal — this fires a goal_created notification
        goal_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=_goal_body(f"{ACCEPTANCE_RUN_ID}-notif-count-check"),
        )
        assert goal_resp.status_code == 201, (
            f"Goal create failed: {goal_resp.status_code} {goal_resp.text}"
        )

        # Count should now be 1
        resp = await client.get(
            "/api/notifications/count",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"count": 1}


# ─── AC3: unauthenticated → 401 ──────────────────────────────────────


async def test_count_rejects_unauthenticated():
    async with make_client() as client:
        resp = await client.get("/api/notifications/count")
    assert resp.status_code == 401