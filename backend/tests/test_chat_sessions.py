"""Tests for POST /api/chat/sessions — create-session endpoint.

These tests verify the child-story scope of D009: persistence + route skeleton
+ create-session endpoint. They do NOT test match behavior, message-turn
processing, create-goal handoff, or request-new-goal-type behavior.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="chat-test@example.com", name="Chat Tester",
                sub="chat-test-sub", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


# ── POST /api/chat/sessions ──────────────────────────────────────────────


async def test_create_chat_session_returns_201_with_session_id_and_messages():
    """POST /api/chat/sessions returns 201 with session_id, messages list,
    and status='active'. The messages list contains exactly one message:
    the assistant greeting specified in api_spec.md."""
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 201
    body = response.json()
    assert "session_id" in body
    assert body["status"] == "active"
    assert "messages" in body
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) == 1
    msg = body["messages"][0]
    assert msg["role"] == "assistant"
    assert msg["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert msg["action"] is None


async def test_create_chat_session_unauthenticated_returns_401():
    """POST /api/chat/sessions without an Authorization header returns 401."""
    async with make_client() as client:
        response = await client.post("/api/chat/sessions")
    assert response.status_code == 401


# ── Persistence ──────────────────────────────────────────────────────────


async def test_create_chat_session_persists_greeting_message_and_status():
    """After creating a session, the stored session in the database contains
    the assistant greeting message and status='active'."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        session_id = resp.json()["session_id"]

        # Retrieve the same session to confirm persistence
        get_resp = await client.get(
            f"/api/chat/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["session_id"] == session_id
    assert body["status"] == "active"
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert body["messages"][0]["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert body["messages"][0]["action"] is None


async def test_create_chat_session_persists_draft_goal_field():
    """A newly created session has draft_goal set to null/None."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        session_id = resp.json()["session_id"]

        get_resp = await client.get(
            f"/api/chat/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert get_resp.status_code == 200
    body = get_resp.json()
    assert "draft_goal" in body
    assert body["draft_goal"] is None


# ── Schema verification ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_sessions_table_exists_with_correct_columns():
    """Verify the chat_sessions table exists in the database with all
    required columns: id, user_id, created_at, updated_at, messages,
    draft_goal, status."""
    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'chat_sessions' ORDER BY ordinal_position"
        ))
        rows = result.fetchall()

    await engine.dispose()

    columns = {row[0]: row[1] for row in rows}

    required = ["id", "user_id", "created_at", "updated_at", "messages", "draft_goal", "status"]
    for col_name in required:
        assert col_name in columns, f"Missing column: {col_name}"

    # messages must be JSONB
    assert columns["messages"].lower() in ("jsonb", "json"), (
        f"messages column should be JSONB, got {columns['messages']}"
    )
    # draft_goal must be JSONB
    assert columns["draft_goal"].lower() in ("jsonb", "json"), (
        f"draft_goal column should be JSONB, got {columns['draft_goal']}"
    )
    # status must be a varchar/enum type
    status_type = columns["status"].lower()
    assert "character" in status_type or "varchar" in status_type or "enum" in status_type or "user-defined" in status_type, (
        f"status column should be varchar/enum, got {columns['status']}"
    )
    # id is UUID
    assert "uuid" in columns["id"].lower(), (
        f"id column should be UUID, got {columns['id']}"
    )