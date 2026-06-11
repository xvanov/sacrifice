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
from sqlalchemy import text

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


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/sessions
# ═══════════════════════════════════════════════════════════════════════════


async def test_create_session_returns_201_with_session_id_messages_status():
    """POST /api/chat/sessions returns 201 with session_id, messages list,
    and status per api_spec.md — requires new chat route to exist."""
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
    # session_id must be a valid UUID string
    assert "session_id" in body
    try:
        uuid.UUID(body["session_id"])
    except (ValueError, TypeError):
        raise AssertionError(
            f"session_id is not a valid UUID: {body['session_id']!r}"
        )
    # messages must contain exactly one assistant greeting
    assert "messages" in body
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) == 1, (
        f"Expected 1 greeting message, got {len(body['messages'])}"
    )
    assert body["messages"][0]["role"] == "assistant"
    assert body["messages"][0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert body["messages"][0]["action"] is None
    # status must be "active"
    assert body["status"] == "active"


async def test_create_session_unauthenticated_returns_401():
    """POST /api/chat/sessions without auth header returns 401 —
    the route must require authentication via get_current_user dependency."""
    async with make_client() as client:
        resp = await client.post("/api/chat/sessions")
    assert resp.status_code == 401, (
        f"Expected 401 for unauthenticated request, got {resp.status_code}"
    )


async def test_create_session_persists_distinct_rows():
    """Two POSTs to /api/chat/sessions create two distinct persisted sessions
    with unique ids — proves the endpoint writes to the chat_sessions table,
    not a static response."""
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

    # Different session ids → each call created a distinct persisted row
    assert body1["session_id"] != body2["session_id"], (
        "Expected two distinct session ids, got the same id for both"
    )

    # Both carry the assistant greeting per spec
    assert body1["messages"][0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert body2["messages"][0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert body1["status"] == "active"
    assert body2["status"] == "active"


async def test_create_session_persists_to_database():
    """After creating a session via the endpoint, the chat_sessions table
    contains the row with the correct user_id, status, and greeting message —
    verifies real database persistence (not in-memory state)."""
    from app.config import settings as cfg
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    async with make_client() as client:
        token, user = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201
    body = resp.json()
    session_id = body["session_id"]

    # Verify the row exists in the real database via a throwaway engine
    verify_engine = create_async_engine(cfg.database_url, echo=False)
    try:
        maker = async_sessionmaker(verify_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db_session:
            row = await db_session.execute(
                text("SELECT id, user_id, status, messages FROM chat_sessions WHERE id = :id"),
                {"id": uuid.UUID(session_id)},
            )
            row = row.fetchone()
    finally:
        await verify_engine.dispose()

    assert row is not None, (
        f"Session {session_id} not found in chat_sessions table"
    )
    assert str(row[1]) == user["id"], (
        f"Expected user_id={user['id']}, got {row[1]}"
    )
    assert row[2] == "active"
    messages = row[3]
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert messages[0]["action"] is None


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/sessions/{session_id}/request-new-goal-type  (STUB)
# ═══════════════════════════════════════════════════════════════════════════


async def test_request_new_goal_type_returns_501():
    """POST /api/chat/sessions/{id}/request-new-goal-type returns 501 with
    the D009 stub message — the real implementation arrives in D010."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # First create a session (need a real one for 501 to be reachable)
        create_resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201, (
            f"Setup failed: expected 201, got {create_resp.status_code}"
        )
        session_id = create_resp.json()["session_id"]

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "Track that I drank 8 glasses of water"},
        )

    assert resp.status_code == 501, (
        f"Expected 501 stub, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == "Goal-type generation is delivered in D010"


async def test_request_new_goal_type_unauthenticated_returns_401():
    """POST /api/chat/sessions/{id}/request-new-goal-type without auth
    returns 401 — the auth guard runs before the stub logic."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 401, (
        f"Expected 401 for unauthenticated request, got {resp.status_code}"
    )


async def test_request_new_goal_type_session_not_found_returns_404():
    """POST /api/chat/sessions/{id}/request-new-goal-type with an
    authenticated user but a nonexistent session id returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.post(
            f"/api/chat/sessions/{fake_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 404, (
        f"Expected 404 for nonexistent session, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == "Session not found"


async def test_request_new_goal_type_non_uuid_session_id_returns_404():
    """POST /api/chat/sessions/{id}/request-new-goal-type with a non-UUID
    session id returns 404 (not 500) — the route validates UUID format."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/not-a-valid-uuid/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 404, (
        f"Expected 404 for non-UUID session id, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == "Session not found"
