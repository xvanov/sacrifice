"""Tests for D009 child-story chat endpoints.

Scope (per story "Child story scope"):
  - POST /api/chat/sessions           (create + greeting)
  - POST /api/chat/sessions/{id}/request-new-goal-type  (stub → 501)
  - Auth (401) and not-found (404) for both

Out of scope for this slice (tested in parent story):
  - POST /api/chat/sessions/{id}/messages
  - POST /api/chat/sessions/{id}/create-goal
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
# POST /api/chat/sessions
# ═══════════════════════════════════════════════════════════════════════════


async def test_create_session_returns_201_with_session_id_messages_status():
    """POST /api/chat/sessions returns 201 with session_id, messages list,
    and status per api_spec.md."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201, (
        f"Expected 201, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "session_id" in body
    try:
        uuid.UUID(body["session_id"])
    except (ValueError, TypeError):
        raise AssertionError(
            f"session_id is not a valid UUID: {body['session_id']!r}"
        )
    assert "messages" in body
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert body["messages"][0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert body["messages"][0]["action"] is None
    assert body["status"] == "active"


async def test_create_session_unauthenticated_returns_401():
    """POST /api/chat/sessions without auth returns 401."""
    async with make_client() as client:
        resp = await client.post("/api/chat/sessions")
    assert resp.status_code == 401


async def test_create_session_persists_greeting_and_status():
    """Two consecutive session creations each get their own greeting and
    active status — proving the server persists (not a static response)."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp1 = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp2 = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp1.status_code == 201
    assert resp2.status_code == 201
    body1 = resp1.json()
    body2 = resp2.json()

    # Different session ids → each call created a distinct persisted row.
    assert body1["session_id"] != body2["session_id"]

    # Both have the greeting message.
    assert body1["messages"][0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert body2["messages"][0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert body1["status"] == "active"
    assert body2["status"] == "active"


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/sessions/{session_id}/request-new-goal-type  (STUB)
# ═══════════════════════════════════════════════════════════════════════════


async def test_request_new_goal_type_returns_501():
    """POST /api/chat/sessions/{id}/request-new-goal-type returns 501 —
    the D009 stub that D010 replaces."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "Track that I drank 8 glasses of water"},
        )

    assert resp.status_code == 501
    assert resp.json()["detail"] == "Goal-type generation is delivered in D010"


async def test_request_new_goal_type_unauthenticated_returns_401():
    """POST /api/chat/sessions/{id}/request-new-goal-type without auth
    returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 401


async def test_request_new_goal_type_session_not_found_returns_404():
    """POST /api/chat/sessions/{id}/request-new-goal-type with a valid UUID
    that matches no session returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.post(
            f"/api/chat/sessions/{fake_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not found"
