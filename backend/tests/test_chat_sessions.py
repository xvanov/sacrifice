"""Tests for D009 chat endpoints that are NOT yet implemented.

These tests cover the api_spec.md endpoints that return 404 today:
  - POST /api/chat/sessions/{session_id}/messages
  - POST /api/chat/sessions/{session_id}/create-goal

Every test MUST fail (RED) on first run because the routes do not exist yet.
"""

import uuid

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="chat-test@example.com", name="Chat Tester",
                sub="chat-test-sub", token="valid-token"):
    from unittest.mock import patch

    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_session(client, token):
    """Create a chat session and return its session_id."""
    resp = await client.post(
        "/api/chat/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, (
        f"Setup failed: expected 201, got {resp.status_code}"
    )
    return resp.json()["session_id"]


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/sessions/{session_id}/messages
# ═══════════════════════════════════════════════════════════════════════════


async def test_post_message_returns_200_with_messages_and_draft_goal():
    """POST /api/chat/sessions/{id}/messages returns 200 with a messages
    list and a draft_goal object per api_spec.md."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "I want to upload a YouTube walkthrough by Friday and pledge $20"},
        )

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "messages" in body
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) >= 2  # user message + assistant reply
    # Last message must be from the assistant
    assert body["messages"][-1]["role"] == "assistant"
    # draft_goal key must be present
    assert "draft_goal" in body


async def test_post_message_unauthenticated_returns_401():
    """POST /api/chat/sessions/{id}/messages without auth returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/messages",
            json={"content": "test"},
        )
    assert resp.status_code == 401


async def test_post_message_session_not_found_returns_404():
    """A valid UUID that matches no session returns 404 with the
    'Session not found' detail from _get_owned_session."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.post(
            f"/api/chat/sessions/{fake_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "test"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not found"


async def test_post_message_not_owned_by_user_returns_403():
    """A session owned by User A must reject User B's message with 403."""
    async with make_client() as client:
        token_a, _ = await _auth(client)
        session_id = await _create_session(client, token_a)

        token_b, _ = await _auth(
            client,
            email="other@test.com",
            name="Other User",
            sub="other-sub",
            token="other-token",
        )
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"content": "test"},
        )
    assert resp.status_code == 403


async def test_post_message_empty_content_returns_422():
    """Empty or whitespace-only content returns 422 per api_spec.md."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "   "},
        )
    assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/sessions/{session_id}/create-goal
# ═══════════════════════════════════════════════════════════════════════════


async def test_create_goal_from_session_returns_201():
    """POST /api/chat/sessions/{id}/create-goal returns 201 with goal_id
    and status per api_spec.md."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "goal_payload": {
                    "title": "YouTube walkthrough",
                    "description": "Upload a walkthrough video",
                    "goal_type": "youtube_video",
                    "pledge_amount": 2000,
                    "currency": "usd",
                    "deadline": "2026-05-29T17:00:00Z",
                    "timezone": "America/New_York",
                    "charity_id": "acct_charity123",
                    "criteria": {
                        "criteria_type": "youtube",
                        "criteria_data": {
                            "min_duration_seconds": 300,
                            "video_description": "A walkthrough demo",
                        },
                    },
                }
            },
        )

    assert resp.status_code == 201, (
        f"Expected 201, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "goal_id" in body
    try:
        uuid.UUID(body["goal_id"])
    except (ValueError, TypeError):
        raise AssertionError(f"goal_id is not a valid UUID: {body['goal_id']!r}")
    assert body["status"] == "draft"


async def test_create_goal_unauthenticated_returns_401():
    """POST /api/chat/sessions/{id}/create-goal without auth returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/create-goal",
            json={"goal_payload": {}},
        )
    assert resp.status_code == 401


async def test_create_goal_session_not_found_returns_404():
    """A valid UUID that matches no session returns 404 with the
    'Session not found' detail from _get_owned_session."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.post(
            f"/api/chat/sessions/{fake_id}/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "goal_payload": {
                    "title": "test",
                    "goal_type": "youtube_video",
                    "pledge_amount": 100,
                    "deadline": "2026-06-01T00:00:00Z",
                    "criteria": {
                        "criteria_type": "youtube",
                        "criteria_data": {"min_duration_seconds": 60},
                    },
                }
            },
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not found"


async def test_create_goal_invalid_payload_returns_422():
    """An empty goal_payload that fails GoalCreate validation returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json={"goal_payload": {}},
        )
    assert resp.status_code == 422
