"""Tests for D010: awaiting_goal_type status, awaiting_direction_id column,
and related lifecycle behaviors.

Uses the conftest.py test_db fixture (ASGI transport + real DB) so every
test exercises the same database session that the app uses.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.goal import Goal
from app.models.user import User


def _client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


# ---------------------------------------------------------------------------
# Persistence round-trip tests — status and awaiting_direction_id survive
# a write → fresh-read cycle through the API (not the same ORM instance).
# ---------------------------------------------------------------------------

async def test_awaiting_goal_type_status_persists(test_db):
    """A goal created with awaiting_goal_type status preserves that status
    when read back through a fresh API GET (not the creating session)."""
    async with _client() as client:
        token, _ = await _auth(client)

        # Create a draft goal first
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Pushup Counter Goal",
                "description": "20 pushups every morning",
                "deadline": "2026-06-01T00:00:00Z",
                "pledge_amount": 1000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 60},
            },
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        # Transition to awaiting_goal_type via PUT
        put_resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["status"] == "awaiting_goal_type"

        # Re-fetch through a fresh GET — must still be awaiting_goal_type
        get_resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["status"] == "awaiting_goal_type", (
            f"Expected awaiting_goal_type, got {body['status']}"
        )
        assert body["id"] == goal_id


async def test_awaiting_direction_id_nullable(test_db):
    """awaiting_direction_id accepts NULL and non-NULL values and both
    survive a round-trip through a fresh API GET."""
    async with _client() as client:
        token, _ = await _auth(client)

        base = {
            "title": "Direction ID Test",
            "description": "nullable column check",
            "deadline": "2026-06-01T00:00:00Z",
            "pledge_amount": 1000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 60},
        }

        # Goal A — with awaiting_direction_id set
        resp_a = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=base,
        )
        assert resp_a.status_code == 201
        goal_a_id = resp_a.json()["id"]

        put_a = await client.put(
            f"/api/goals/{goal_a_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "status": "awaiting_goal_type",
                "awaiting_direction_id": "011-pushup-counter",
            },
        )
        assert put_a.status_code == 200

        # Goal B — without awaiting_direction_id (NULL)
        resp_b = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={**base, "title": "Null Direction ID Goal"},
        )
        assert resp_b.status_code == 201
        goal_b_id = resp_b.json()["id"]

        put_b = await client.put(
            f"/api/goals/{goal_b_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert put_b.status_code == 200

        # Re-fetch both goals in fresh GETs
        get_a = await client.get(
            f"/api/goals/{goal_a_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        get_b = await client.get(
            f"/api/goals/{goal_b_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert get_a.status_code == 200
        assert get_a.json()["awaiting_direction_id"] == "011-pushup-counter"

        assert get_b.status_code == 200
        assert get_b.json()["awaiting_direction_id"] is None


async def test_awaiting_goal_type_serialized_in_goal_response(test_db):
    """A goal in awaiting_goal_type status is serialized correctly in the
    API response, including the awaiting_direction_id field."""
    async with _client() as client:
        token, _ = await _auth(client)

        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Serialization Test Goal",
                "description": "Check serialization",
                "deadline": "2026-06-01T00:00:00Z",
                "pledge_amount": 2000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 120},
            },
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        # Put into awaiting_goal_type with a direction id
        put_resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "status": "awaiting_goal_type",
                "awaiting_direction_id": "022-widget-tracker",
            },
        )
        assert put_resp.status_code == 200, (
            f"Expected 200 for draft→awaiting_goal_type, got {put_resp.status_code}: {put_resp.text}"
        )

        # GET the goal and verify serialization
        get_resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        body = get_resp.json()

        assert body["status"] == "awaiting_goal_type"
        assert body["awaiting_direction_id"] == "022-widget-tracker"
        assert body["goal_type"] == "youtube_video"
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body


# ---------------------------------------------------------------------------
# Lifecycle boundary tests — transitions that the story requires but are
# NOT yet implemented in production code.
# ---------------------------------------------------------------------------

async def test_awaiting_goal_type_transitions_to_active(test_db):
    """A goal in awaiting_goal_type can transition to active — the accept
    path from the story.  This MUST fail until ALLOWED_TRANSITIONS is
    updated."""
    async with _client() as client:
        token, _ = await _auth(client)

        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Acceptance Transition Test",
                "description": "Should go from awaiting_goal_type to active",
                "deadline": "2026-06-01T00:00:00Z",
                "pledge_amount": 1000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 60},
            },
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        # Move into awaiting_goal_type
        put1 = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "status": "awaiting_goal_type",
                "awaiting_direction_id": "011-pushup-counter",
            },
        )
        assert put1.status_code == 200
        assert put1.json()["status"] == "awaiting_goal_type"

        # The accept path: awaiting_goal_type → active
        put2 = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        assert put2.status_code == 200, (
            f"Expected 200 for awaiting_goal_type→active, got {put2.status_code}: {put2.text}"
        )
        body = put2.json()
        assert body["status"] == "active"

        # Confirm via fresh GET
        get_resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "active"


async def test_deadline_worker_skips_awaiting_goal_type(test_db):
    """check_deadlines does NOT charge or transition goals that are in
    awaiting_goal_type status, even when their deadline has passed.
    This MUST fail until the deadline worker explicitly filters out the
    new status (the worker currently only queries active/pending_review,
    so it implicitly skips awaiting_goal_type — the story requires an
    explicit guard)."""
    from app.workers.deadline import check_deadlines

    # Use the DI-overridden session from conftest so data is visible to
    # check_deadlines (which creates its own engine against the same DB).
    from app.database import get_db

    async for db_session in get_db():
        break

    user = User(
        email="deadline-skip@test.com",
        display_name="Deadline Skip Tester",
        auth_provider="google",
        auth_provider_id="google-deadline-skip-d010",
    )
    db_session.add(user)
    await db_session.commit()
    user_id = user.id

    past = datetime.now(timezone.utc) - timedelta(days=2)
    goal = Goal(
        user_id=user_id,
        title="Past Deadline Awaiting Goal",
        goal_type="youtube_video",
        pledge_amount=1000,
        deadline=past,
        status="awaiting_goal_type",
        awaiting_direction_id="011-pushup-counter",
    )
    db_session.add(goal)
    await db_session.commit()
    goal_id = goal.id

    # Regression guard: an active goal with a past deadline should still
    # be processed normally.
    active_goal = Goal(
        user_id=user_id,
        title="Past Deadline Active Goal",
        goal_type="youtube_video",
        pledge_amount=500,
        deadline=past,
        status="active",
    )
    db_session.add(active_goal)
    await db_session.commit()
    active_goal_id = active_goal.id

    await check_deadlines()

    # Re-query both goals from a fresh session
    from app.database import get_db as fresh_get_db
    async for fresh_session in fresh_get_db():
        break

    from sqlalchemy import select as sa_select
    res = await fresh_session.execute(sa_select(Goal).where(Goal.id == goal_id))
    awaiting_goal = res.scalar_one()
    res2 = await fresh_session.execute(sa_select(Goal).where(Goal.id == active_goal_id))
    active_after = res2.scalar_one()

    # awaiting_goal_type goal MUST NOT have been touched
    assert awaiting_goal.status == "awaiting_goal_type", (
        f"awaiting_goal_type goal was transitioned to {awaiting_goal.status} — "
        f"deadline worker should skip it"
    )

    # active goal MUST have been processed (no longer active).
    # The exact terminal status depends on whether Stripe is configured,
    # but it must not still be 'active'.
    assert active_after.status != "active", (
        f"active goal with past deadline was NOT processed — still {active_after.status}"
    )


# ---------------------------------------------------------------------------
# request-new-goal-type endpoint — the story's core API path.
# ---------------------------------------------------------------------------

async def test_request_new_goal_type_returns_202(test_db):
    """POST /api/chat/sessions/{session_id}/request-new-goal-type creates
    a goal in awaiting_goal_type with an awaiting_direction_id and returns
    the direction_id + goal_id.  MUST fail (404) until the endpoint exists."""
    async with _client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/chat/sessions/fake-session-id/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                "goal_payload_draft": {
                    "title": "20 morning pushups",
                    "description": "Do 20 pushups every morning at 7am, verified with phone camera.",
                    "pledge_amount": 1000,
                    "currency": "usd",
                    "deadline": "2026-05-26T11:00:00Z",
                    "timezone": "America/New_York",
                    "charity_id": "acct_charity123",
                    "recurrence": "daily",
                },
            },
        )
        assert resp.status_code == 202, (
            f"Expected 202, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "direction_id" in body
        assert "goal_id" in body
        assert body["status"] == "queued"

        # Verify the goal was created in awaiting_goal_type with correct linkage
        goal_id = body["goal_id"]
        get_resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        goal = get_resp.json()
        assert goal["status"] == "awaiting_goal_type"
        assert goal["awaiting_direction_id"] == body["direction_id"]


async def test_request_new_goal_type_rejects_duplicate_in_flight(test_db):
    """A second request-new-goal-type while one is already in-flight
    returns 409 with the existing direction_id.  MUST fail until endpoint
    exists."""
    async with _client() as client:
        token, _ = await _auth(client)

        payload = {
            "prompt_summary": "Do 20 pushups every morning at 7am",
            "goal_payload_draft": {
                "title": "20 morning pushups",
                "description": "Pushups verification",
                "pledge_amount": 1000,
                "currency": "usd",
                "deadline": "2026-05-26T11:00:00Z",
                "timezone": "America/New_York",
                "charity_id": "acct_charity123",
                "recurrence": "daily",
            },
        }

        # First request — should succeed (202)
        resp1 = await client.post(
            "/api/chat/sessions/fake-session-id/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert resp1.status_code == 202, (
            f"First request: expected 202, got {resp1.status_code}"
        )
        first_direction_id = resp1.json()["direction_id"]

        # Second request — should be rejected (409)
        resp2 = await client.post(
            "/api/chat/sessions/fake-session-id/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert resp2.status_code == 409, (
            f"Second request: expected 409, got {resp2.status_code}: {resp2.text}"
        )
        body2 = resp2.json()
        assert "direction_id" in body2
        assert body2["direction_id"] == first_direction_id