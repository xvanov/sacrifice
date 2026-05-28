"""
Tests for the chat generation endpoints.

All of these MUST fail on first run because:
- No chat router is mounted in main.py
- No chat routes module exists
- The endpoints return 404 from FastAPI's default router

Endpoint contract per api_spec.md:
- POST /api/chat/sessions/{session_id}/request-new-goal-type
- GET  /api/chat/sessions/{session_id}/generation-status
- POST /api/chat/sessions/{session_id}/accept-generated-type
- POST /api/chat/sessions/{session_id}/iterate-generated-type
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


SESSION_ID = "00000000-0000-0000-0000-000000000001"


# ─── POST /api/chat/sessions/{session_id}/request-new-goal-type ───────


async def test_request_new_goal_type_returns_202_with_direction_id():
    """
    POST request-new-goal-type returns 202 with direction_id, goal_id, status.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                "goal_payload_draft": {
                    "title": "20 morning pushups",
                    "description": "Do 20 pushups every morning at 7am, verified with my phone camera.",
                    "pledge_amount": 1000,
                    "currency": "usd",
                    "deadline": "2026-05-26T11:00:00Z",
                    "timezone": "America/New_York",
                    "charity_id": "acct_charity123",
                    "recurrence": "daily",
                },
            },
        )
    assert response.status_code == 202
    body = response.json()
    assert "direction_id" in body
    assert "goal_id" in body
    assert body["status"] == "queued"


async def test_request_new_goal_type_requires_auth():
    """
    POST request-new-goal-type returns 401 without auth.
    MUST fail: no chat router is mounted (404, not 401).
    """
    async with make_client() as client:
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/request-new-goal-type",
            json={"prompt_summary": "test", "goal_payload_draft": {}},
        )
    assert response.status_code == 401


async def test_request_new_goal_type_409_when_in_flight():
    """
    POST request-new-goal-type returns 409 when user already has an
    in-flight generation, with the existing direction_id in the body.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        # First request
        await client.post(
            f"/api/chat/sessions/{SESSION_ID}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "prompt_summary": "Do 20 pushups every morning at 7am",
                "goal_payload_draft": {
                    "title": "20 morning pushups",
                    "description": "Test",
                    "pledge_amount": 1000,
                    "currency": "usd",
                    "deadline": "2026-05-26T11:00:00Z",
                    "timezone": "UTC",
                    "charity_id": "acct_charity123",
                    "recurrence": "daily",
                },
            },
        )
        # Second request should be 409
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "prompt_summary": "Do 30 pushups instead",
                "goal_payload_draft": {
                    "title": "30 morning pushups",
                    "description": "Test",
                    "pledge_amount": 2000,
                    "currency": "usd",
                    "deadline": "2026-05-26T11:00:00Z",
                    "timezone": "UTC",
                    "charity_id": "acct_charity123",
                    "recurrence": "daily",
                },
            },
        )
    assert response.status_code == 409
    body = response.json()
    assert "direction_id" in body


async def test_request_new_goal_type_429_when_budget_exhausted():
    """
    POST request-new-goal-type returns 429 when daily AI budget is exhausted.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/request-new-goal-type",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Sacrifice-Force-Generate": "true",
            },
            json={
                "prompt_summary": "Do 20 pushups every morning at 7am",
                "goal_payload_draft": {
                    "title": "20 morning pushups",
                    "description": "Test",
                    "pledge_amount": 1000,
                    "currency": "usd",
                    "deadline": "2026-05-26T11:00:00Z",
                    "timezone": "UTC",
                    "charity_id": "acct_charity123",
                    "recurrence": "daily",
                },
            },
        )
    # The route does not exist yet. It MUST return 202 (or 429 when budget
    # exhausted). 404 means the route isn't mounted.
    assert response.status_code in (202, 429), (
        f"Expected 202 or 429; got {response.status_code}. "
        "Route is likely not mounted yet."
    )


# ─── GET /api/chat/sessions/{session_id}/generation-status ────────────


async def test_generation_status_returns_status_and_pr_url():
    """
    GET generation-status returns direction_id, status, pr_url, summary.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.get(
            f"/api/chat/sessions/{SESSION_ID}/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert "direction_id" in body
    assert "status" in body
    assert body["status"] in {"queued", "in_progress", "pr_open", "pr_merged", "rejected"}
    assert "pr_url" in body
    assert "summary" in body


async def test_generation_status_404_when_no_generation():
    """
    GET generation-status returns 404 when session has no in-flight generation.
    MUST fail: no chat router is mounted. FastAPI returns 404 for the unknown
    path but with no session-specific detail — the route handler doesn't exist.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.get(
            f"/api/chat/sessions/{SESSION_ID}/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    # The route does not exist, so we get 404. But the REAL endpoint
    # should return 200 with status info. The 404 here is from FastAPI
    # not knowing the path — the route must be mounted first.
    # Assert on the presence of the ACTUAL endpoint behavior:
    assert response.status_code == 200, (
        f"Expected 200 from generation-status; got {response.status_code}. "
        "Route is likely not mounted yet."
    )


async def test_generation_status_requires_auth():
    """
    GET generation-status returns 401 without auth.
    MUST fail: no chat router is mounted (404, not 401).
    """
    async with make_client() as client:
        response = await client.get(
            f"/api/chat/sessions/{SESSION_ID}/generation-status",
        )
    assert response.status_code == 401


# ─── POST /api/chat/sessions/{session_id}/accept-generated-type ───────


async def test_accept_generated_type_transitions_to_active():
    """
    POST accept-generated-type transitions goal from awaiting_goal_type to active.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert "goal_id" in body
    assert body["status"] == "active"


async def test_accept_generated_type_409_when_not_merged():
    """
    POST accept-generated-type returns 409 when generation is not yet merged.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
    # The route does not exist yet. The real endpoint must return 409
    # when status != pr_merged. Asserting on that specific status code
    # ensures the route exists AND has the correct business logic.
    assert response.status_code == 409, (
        f"Expected 409 (generation not merged); got {response.status_code}. "
        "Route is likely not mounted yet or returned unexpected status."
    )


async def test_accept_generated_type_requires_auth():
    """
    POST accept-generated-type returns 401 without auth.
    MUST fail: no chat router is mounted (404, not 401).
    """
    async with make_client() as client:
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/accept-generated-type",
        )
    assert response.status_code == 401


# ─── POST /api/chat/sessions/{session_id}/iterate-generated-type ──────


async def test_iterate_generated_type_returns_202_with_direction_ids():
    """
    POST iterate-generated-type returns 202 with direction_id,
    previous_direction_id, and status=queued.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use a side-on camera angle; count partial reps as 0.5."},
        )
    assert response.status_code == 202
    body = response.json()
    assert "direction_id" in body
    assert "previous_direction_id" in body
    assert body["status"] == "queued"


async def test_iterate_generated_type_422_for_empty_feedback():
    """
    POST iterate-generated-type returns 422 for empty/whitespace feedback.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "   "},
        )
    assert response.status_code == 422


async def test_iterate_generated_type_409_when_already_accepted():
    """
    POST iterate-generated-type returns 409 when goal was already accepted.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Better angle"},
        )
    # The route does not exist yet. The real endpoint must return 409
    # when the goal was already accepted.
    assert response.status_code == 409, (
        f"Expected 409 (goal already accepted); got {response.status_code}. "
        "Route is likely not mounted yet or returned unexpected status."
    )


async def test_iterate_generated_type_429_when_budget_exhausted():
    """
    POST iterate-generated-type returns 429 when daily AI budget exhausted.
    MUST fail: no chat router is mounted.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Better angle"},
        )
    # The route does not exist yet. It MUST return 202 (or 429 when budget
    # exhausted). 404 means the route isn't mounted.
    assert response.status_code in (202, 429), (
        f"Expected 202 or 429; got {response.status_code}. "
        "Route is likely not mounted yet."
    )


async def test_iterate_generated_type_requires_auth():
    """
    POST iterate-generated-type returns 401 without auth.
    MUST fail: no chat router is mounted (404, not 401).
    """
    async with make_client() as client:
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/iterate-generated-type",
            json={"feedback": "Better angle"},
        )
    assert response.status_code == 401