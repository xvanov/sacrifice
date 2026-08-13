import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client,
    email="test@example.com",
    name="Test User",
    sub="test-sub-123",
    token="valid-token",
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


VALID_GOAL = {
    "title": "Test Goal",
    "description": "A test goal",
    # Future deadline: activation requires one beyond the minimum lead. Computed at
    # import so the fixture never rots as the wall clock advances.
    "deadline": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    "pledge_amount": 5000,
    "goal_type": "youtube_video",
    "criteria": {
        "min_duration_seconds": 300,
        "video_description": "A walkthrough demo",
    },
    "charity_id": "acct_charity123",
}


async def _create_goal(client, token, overrides=None):
    body = {**VALID_GOAL, **(overrides or {})}
    return await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


async def _activate_goal(client, token, goal_id):
    return await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )


async def _set_goal_status(client, token, goal_id, status):
    return await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": status},
    )


async def _resolve_via_worker(goal_id, status):
    """Resolve a goal the way the real pipeline does — through the verification
    worker's persist step, which sets the status AND emits the notification.

    Users can no longer PUT a goal to verified/failed (that was the pledge
    escape), so tests drive resolution through the worker instead.
    """
    from app.workers.youtube import _persist_result

    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await _persist_result(db, uuid.UUID(goal_id), uuid.uuid4(), status, {})
    await engine.dispose()


async def _verify_goal(client, token, goal_id):
    await _activate_goal(client, token, goal_id)
    await _resolve_via_worker(goal_id, "verified")


async def _fail_goal(client, token, goal_id):
    """Resolve a goal to `failed` the way the real pipeline does.

    A `failed` verdict on a still-``active`` goal no longer resolves the goal
    (or fires the `goal_failed` notification) immediately — the owner gets a
    chance to submit again before the deadline (see verification_result.py's
    "A real failure before the deadline is not yet a verdict on the goal").
    Put the goal in ``pending_review`` first (a status the verification
    pipeline itself would move it to once the submission window is closed) so
    this exercises the terminal path this test is actually about.
    """
    await _activate_goal(client, token, goal_id)
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        from sqlalchemy import text

        await db.execute(
            text("UPDATE goals SET status = 'pending_review' WHERE id = :g"),
            {"g": uuid.UUID(goal_id)},
        )
        await db.commit()
    await engine.dispose()
    await _resolve_via_worker(goal_id, "failed")


# ─── GET /api/notifications ─────────────────────────────────────────


async def test_get_notifications_returns_empty_list_initially():
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json() == []


async def test_get_notifications_returns_paginated_with_limit():
    async with make_client() as client:
        token, _ = await _auth(client)
        # Create 5 goals to generate notifications, each with a delay to get
        # different created_at values
        for i in range(5):
            await _create_goal(client, token, {"title": f"Goal {i}"})

        response = await client.get(
            "/api/notifications?limit=2",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    # Most recent first
    assert body[0]["title"] == "Goal Created: Goal 4"


async def test_get_notifications_isolates_users():
    async with make_client() as client:
        token1, _ = await _auth(client)
        token2, _ = await _auth(
            client,
            email="other@test.com",
            name="Other",
            sub="other-sub",
            token="other-token",
        )
        await _create_goal(client, token1)
        await _create_goal(client, token2)

        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token1}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "goal_created"


async def test_get_notifications_requires_auth():
    async with make_client() as client:
        response = await client.get("/api/notifications")
    assert response.status_code == 401


# ─── GET /api/notifications/unread-count ─────────────────────────────


async def test_unread_count_returns_zero_initially():
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.get(
            "/api/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json() == {"unread_count": 0}


async def test_unread_count_returns_correct_count():
    async with make_client() as client:
        token, _ = await _auth(client)
        await _create_goal(client, token)
        await _create_goal(client, token, {"title": "Second goal"})

        response = await client.get(
            "/api/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json() == {"unread_count": 2}


async def test_unread_count_decreases_after_marking_read():
    async with make_client() as client:
        token, _ = await _auth(client)
        await _create_goal(client, token)

        notifs_resp = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
        notif_id = notifs_resp.json()[0]["id"]

        await client.put(
            f"/api/notifications/{notif_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )

        response = await client.get(
            "/api/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.json() == {"unread_count": 0}


# ─── PUT /api/notifications/{id}/read ────────────────────────────────


async def test_mark_notification_as_read():
    async with make_client() as client:
        token, _ = await _auth(client)
        await _create_goal(client, token)

        notifs = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
        notif_id = notifs.json()[0]["id"]
        assert notifs.json()[0]["read"] is False

        response = await client.put(
            f"/api/notifications/{notif_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_mark_notification_as_read_returns_404_for_invalid_id():
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.put(
            "/api/notifications/00000000-0000-0000-0000-000000000000/read",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404


async def test_mark_notification_as_read_isolates_users():
    async with make_client() as client:
        token1, _ = await _auth(client)
        token2, _ = await _auth(
            client,
            email="other@test.com",
            name="Other",
            sub="other-sub",
            token="other-token",
        )
        await _create_goal(client, token1)
        await _create_goal(client, token2)

        notifs = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token2}"},
        )
        notif_id = notifs.json()[0]["id"]

        response = await client.put(
            f"/api/notifications/{notif_id}/read",
            headers={"Authorization": f"Bearer {token1}"},
        )
    assert response.status_code == 404


# ─── PUT /api/notifications/read-all ─────────────────────────────────


async def test_mark_all_notifications_as_read():
    async with make_client() as client:
        token, _ = await _auth(client)
        await _create_goal(client, token)
        await _create_goal(client, token, {"title": "Second goal"})

        response = await client.put(
            "/api/notifications/read-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        unread = await client.get(
            "/api/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert unread.status_code == 200
        assert unread.json() == {"unread_count": 0}


# ─── Auto-notifications on goal events ───────────────────────────────


async def test_goal_created_auto_creates_notification():
    async with make_client() as client:
        token, _ = await _auth(client)
        await _create_goal(client, token)

        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "goal_created"
    assert body[0]["title"] == "Goal Created: Test Goal"
    assert "goal_id" in body[0]
    assert body[0]["read"] is False


async def test_proof_submitted_auto_creates_notification():
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await _create_goal(client, token)
        goal_id = resp.json()["id"]
        await _activate_goal(client, token, goal_id)

        # The dispatch happens in the youtube worker module (via the plugin's
        # dispatch_verification), not in routes.goals. Patch it there so no real
        # Celery task is enqueued.
        with patch("app.workers.youtube.run_youtube_verification_task"):
            await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://youtube.com/watch?v=dQw4w9WgXcQ"},
            )

        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    # Should have goal_created + proof_received
    assert len(body) == 2
    assert body[0]["type"] == "proof_received"
    assert "proof" in body[0]["title"].lower()


async def test_goal_verified_auto_creates_notification():
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await _create_goal(client, token)
        goal_id = resp.json()["id"]

        await _verify_goal(client, token, goal_id)

        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    types = [n["type"] for n in body]
    assert "goal_completed" in types
    notif = [n for n in body if n["type"] == "goal_completed"][0]
    assert "completed" in notif["title"].lower()


async def test_goal_failed_auto_creates_notification():
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await _create_goal(client, token)
        goal_id = resp.json()["id"]

        await _fail_goal(client, token, goal_id)

        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    types = [n["type"] for n in body]
    assert "goal_failed" in types
    notif = [n for n in body if n["type"] == "goal_failed"][0]
    assert "failed" in notif["title"].lower()
    # Should mention the pledge amount in the body
    assert "$" in notif["body"] or "pledge" in notif["body"].lower()


async def test_notifications_sorted_by_created_at_desc():
    async with make_client() as client:
        token, _ = await _auth(client)
        await _create_goal(client, token, {"title": "First"})
        await _create_goal(client, token, {"title": "Second"})
        await _create_goal(client, token, {"title": "Third"})

        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    # Most recent first
    titles = [n["title"] for n in body]
    assert "Goal Created: Third" in titles[0]
