"""Tests for D010 chat session endpoints.

Covers:
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


REQUEST_BODY = {
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
}


# ─── POST request-new-goal-type ─────────────────────────────────────


async def test_request_new_goal_type_returns_202_with_direction_id():
    """POST request-new-goal-type returns 202 with direction_id, goal_id, status=queued."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=REQUEST_BODY,
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "direction_id" in body
    assert isinstance(body["direction_id"], str)
    assert "goal_id" in body
    assert body["status"] == "queued"


async def test_request_new_goal_type_requires_auth():
    """POST request-new-goal-type without token returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/request-new-goal-type",
            json=REQUEST_BODY,
        )
    assert resp.status_code == 401


async def test_request_new_goal_type_nonexistent_session_returns_404():
    """POST request-new-goal-type for a session that doesn't exist returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/nonexistent-session/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=REQUEST_BODY,
        )
    assert resp.status_code == 404
    body = resp.json()
    assert "session" in body.get("detail", "").lower()


async def test_request_new_goal_type_vague_prompt_returns_422():
    """POST request-new-goal-type with a vague prompt_summary returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        vague_body = {**REQUEST_BODY, "prompt_summary": "do something idk"}
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=vague_body,
        )
    assert resp.status_code == 422


async def test_request_new_goal_type_duplicate_in_flight_returns_409():
    """POST request-new-goal-type when user already has in-flight generation returns 409."""
    async with make_client() as client:
        token, _ = await _auth(client)
        # First request succeeds
        await client.post(
            "/api/chat/sessions/fake-session-id/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=REQUEST_BODY,
        )
        # Second request should conflict
        resp = await client.post(
            "/api/chat/sessions/fake-session-id-2/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=REQUEST_BODY,
        )
    assert resp.status_code == 409
    body = resp.json()
    assert "direction_id" in body


# ─── GET generation-status ──────────────────────────────────────────


async def test_generation_status_returns_200_with_fields():
    """GET generation-status returns 200 with direction_id, status, pr_url, summary."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.get(
            "/api/chat/sessions/fake-session-id/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "direction_id" in body
    assert "status" in body
    assert body["status"] in {"queued", "in_progress", "pr_open", "pr_merged", "rejected"}
    assert "pr_url" in body
    assert "summary" in body


async def test_generation_status_no_in_flight_returns_404():
    """GET generation-status for a session with no in-flight generation returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.get(
            "/api/chat/sessions/nonexistent-session/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
    body = resp.json()
    assert "session" in body.get("detail", "").lower()


async def test_generation_status_requires_auth():
    """GET generation-status without token returns 401."""
    async with make_client() as client:
        resp = await client.get(
            "/api/chat/sessions/fake-session-id/generation-status",
        )
    assert resp.status_code == 401


# ─── POST accept-generated-type ─────────────────────────────────────


async def test_accept_generated_type_returns_200_active():
    """POST accept-generated-type returns 200 with goal_id and status=active."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "goal_id" in body
    assert body["status"] == "active"


async def test_accept_generated_type_requires_auth():
    """POST accept-generated-type without token returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/accept-generated-type",
        )
    assert resp.status_code == 401


async def test_accept_generated_type_nonexistent_session_returns_404():
    """POST accept-generated-type for nonexistent session returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/nonexistent-session/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
    body = resp.json()
    assert "session" in body.get("detail", "").lower()


async def test_accept_generated_type_not_merged_returns_409():
    """POST accept-generated-type when generation is not yet merged returns 409."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


# ─── POST iterate-generated-type ────────────────────────────────────


async def test_iterate_generated_type_returns_202_with_direction_ids():
    """POST iterate-generated-type returns 202 with direction_id, previous_direction_id, status."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use a side-on camera angle; count partial reps as 0.5."},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "direction_id" in body
    assert "previous_direction_id" in body
    assert body["status"] == "queued"


async def test_iterate_generated_type_requires_auth():
    """POST iterate-generated-type without token returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/iterate-generated-type",
            json={"feedback": "Use side angle"},
        )
    assert resp.status_code == 401


async def test_iterate_generated_type_nonexistent_session_returns_404():
    """POST iterate-generated-type for nonexistent session returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/nonexistent-session/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use side angle"},
        )
    assert resp.status_code == 404
    body = resp.json()
    assert "session" in body.get("detail", "").lower()


async def test_iterate_generated_type_already_accepted_returns_409():
    """POST iterate-generated-type after acceptance returns 409."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use side angle"},
        )
    assert resp.status_code == 409


async def test_iterate_generated_type_empty_feedback_returns_422():
    """POST iterate-generated-type with empty feedback returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "   "},
        )
    assert resp.status_code == 422


async def test_iterate_generated_type_spend_cap_exceeded_returns_429():
    """POST iterate-generated-type when daily AI budget exceeded returns 429."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/fake-session-id/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use side angle"},
        )
    assert resp.status_code == 429