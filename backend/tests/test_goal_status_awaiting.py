"""
Tests for awaiting_goal_type status and awaiting_direction_id column.

These tests assert on new model/schema behavior that does not exist yet:
- ``awaiting_goal_type`` is not in the Goal.status enum
- ``awaiting_direction_id`` column does not exist on the goals table
- The GoalResponse schema does not serialize ``awaiting_direction_id``

Every test in this file MUST fail on first run.
"""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


VALID_GOAL = {
    "title": "20 morning pushups",
    "description": "Do 20 pushups every morning at 7am, verified with phone camera.",
    "deadline": "2026-05-26T11:00:00Z",
    "pledge_amount": 1000,
    "goal_type": "youtube_video",
    "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
    "charity_id": "acct_charity123",
}


# ─── awaiting_goal_type status persistence ────────────────────────────


async def test_create_goal_with_awaiting_goal_type_status():
    """
    Creating a goal with status='awaiting_goal_type' persists correctly.
    This MUST fail: the Goal.status enum does not include awaiting_goal_type yet.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={**VALID_GOAL, "goal_type": "youtube_video"},
        )
        assert response.status_code == 201
        goal_id = response.json()["id"]

        # Transition to awaiting_goal_type — this must fail pre-implementation
        # because the enum and state machine don't support it yet.
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_goal_type"


async def test_awaiting_goal_type_goal_is_retrievable():
    """
    A goal with status='awaiting_goal_type' is returned in list and detail views.
    This MUST fail: the status value doesn't exist yet.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={**VALID_GOAL, "goal_type": "youtube_video"},
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        # Set to awaiting_goal_type
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert resp.status_code == 200

        # Detail view
        detail = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == "awaiting_goal_type"

        # List view includes it
        all_goals = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert all_goals.status_code == 200
        statuses = [g["status"] for g in all_goals.json()]
        assert "awaiting_goal_type" in statuses


# ─── awaiting_direction_id column ─────────────────────────────────────


async def test_goal_with_awaiting_direction_id():
    """
    A goal can be created with an awaiting_direction_id and the value is
    returned in the serialized response.
    This MUST fail: the column doesn't exist on the goals table yet.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={**VALID_GOAL, "goal_type": "youtube_video"},
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        # Set status to awaiting_goal_type and direction_id
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "status": "awaiting_goal_type",
                "awaiting_direction_id": "011-pushup-counter",
            },
        )
        assert resp.status_code == 200

        # Confirm direction_id is serialized
        detail = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["awaiting_direction_id"] == "011-pushup-counter"


async def test_awaiting_direction_id_is_nullable():
    """
    A goal without an awaiting_direction_id has it as null in the response.
    This MUST fail: the column doesn't exist yet.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={**VALID_GOAL, "goal_type": "youtube_video"},
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        detail = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["awaiting_direction_id"] is None


async def test_awaiting_direction_id_persists_across_updates():
    """
    awaiting_direction_id survives unrelated field updates.
    This MUST fail: the column doesn't exist yet.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={**VALID_GOAL, "goal_type": "youtube_video"},
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        # Set direction
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "status": "awaiting_goal_type",
                "awaiting_direction_id": "011-pushup-counter",
            },
        )
        assert resp.status_code == 200

        # Update title only
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Updated pushup goal"},
        )
        assert resp.status_code == 200

        # Direction should still be there
        detail = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["awaiting_direction_id"] == "011-pushup-counter"
        assert body["title"] == "Updated pushup goal"