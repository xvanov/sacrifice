"""Tests for the D009 chat-sessions slice: persistence + route skeleton
+ create-session endpoint + request-new-goal-type stub.

These tests verify the child-story scope of D009. They do NOT test match
behavior, message-turn processing, or create-goal handoff.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app

CHAT_SESSIONS_FQN = "chat_sessions"


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


def _db_engine():
    return create_async_engine(settings.database_url, echo=False)


async def _fetch_row(session_id: str):
    """Return the raw chat_sessions row as a dict or None."""
    engine = _db_engine()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(f"SELECT * FROM {CHAT_SESSIONS_FQN} WHERE id = :sid"),
                {"sid": session_id},
            )
            row = result.mappings().first()
            return dict(row) if row is not None else None
    finally:
        await engine.dispose()


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


# ── Persistence (direct-DB verification) ─────────────────────────────────


async def test_create_chat_session_persists_greeting_message_and_status():
    """After creating a session, query the chat_sessions row directly and
    assert user_id, messages, status, and draft_goal match the stored state."""
    async with make_client() as client:
        token, user = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        session_id = body["session_id"]

    row = await _fetch_row(session_id)
    assert row is not None, "session row must exist in database"
    assert str(row["user_id"]) == user["id"]
    assert row["status"] == "active"
    assert row["draft_goal"] is None
    assert isinstance(row["messages"], list)
    assert len(row["messages"]) == 1
    msg = row["messages"][0]
    assert msg["role"] == "assistant"
    assert msg["content"] == (
        "Tell me what you want to do, and I'll figure out how to track it."
    )
    assert msg["action"] is None


# ── Schema verification: constraints + downgrade ─────────────────────────


@pytest.mark.asyncio
async def test_chat_sessions_status_constraint_allows_only_valid_values():
    """The chat_sessions.status column is backed by a PostgreSQL enum (or
    CHECK constraint) that only accepts 'active', 'goal_created', and
    'awaiting_goal_type'."""
    engine = _db_engine()
    try:
        async with engine.connect() as conn:
            # Check for the enum type created by SQLAlchemy's Enum()
            result = await conn.execute(text(
                "SELECT enum_range(NULL::chat_session_status)::text[]"
            ))
            enum_vals = result.scalar()
            if enum_vals:
                # PostgreSQL enum — verify the exact set
                assert set(enum_vals) == {"active", "goal_created", "awaiting_goal_type"}, (
                    f"unexpected enum values: {enum_vals}"
                )
            else:
                # Fallback: check for a CHECK constraint on the column
                result = await conn.execute(text(
                    "SELECT pg_get_constraintdef(c.oid) "
                    "FROM pg_constraint c "
                    "JOIN pg_class t ON c.conrelid = t.oid "
                    "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey) "
                    "WHERE t.relname = 'chat_sessions' "
                    "  AND a.attname = 'status' "
                    "  AND c.contype = 'c'"
                ))
                constraint_def = result.scalar()
                assert constraint_def is not None, (
                    "status column must have a CHECK or enum constraint"
                )
                assert "active" in constraint_def
                assert "goal_created" in constraint_def
                assert "awaiting_goal_type" in constraint_def

            # Verify the column itself exists
            result = await conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :tbl AND column_name = 'status'"
            ), {"tbl": CHAT_SESSIONS_FQN})
            row = result.fetchone()
            assert row is not None, "status column must exist"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chat_sessions_migration_downgrade_removes_table():
    """After running alembic downgrade one step, the chat_sessions table
    no longer exists in the database."""
    import subprocess
    import sys

    # Verify table exists before downgrade
    engine = _db_engine()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :tbl)"
            ), {"tbl": CHAT_SESSIONS_FQN})
            assert result.scalar(), "chat_sessions must exist before downgrade test"
    finally:
        await engine.dispose()

    # Stamp head so alembic knows the current state (tables were created by
    # conftest via Base.metadata.create_all, not by alembic migrations).
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "stamp", "head"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"stamp failed: {result.stderr}"
    )

    # Downgrade one step: chat_sessions was added in 74b288f75c85.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"downgrade failed: {result.stderr}"
    )

    # Verify table is gone
    engine2 = _db_engine()
    try:
        async with engine2.connect() as conn:
            result = await conn.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :tbl)"
            ), {"tbl": CHAT_SESSIONS_FQN})
            assert not result.scalar(), (
                "chat_sessions must NOT exist after downgrade"
            )
    finally:
        await engine2.dispose()

    # Run upgrade back so subsequent tests aren't broken
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"upgrade failed: {result.stderr}"
    )


# ── POST /api/chat/sessions/{session_id}/request-new-goal-type (STUB) ───


async def test_request_new_goal_type_returns_501_not_implemented():
    """POST /api/chat/sessions/{id}/request-new-goal-type returns 501 with
    a detail message indicating D010 supersedes."""
    async with make_client() as client:
        token, _ = await _auth(client)
        # First create a session so we have a valid id
        resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        session_id = resp.json()["session_id"]

        stub_resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            json={"prompt_summary": "Track that I drank 8 glasses of water today"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert stub_resp.status_code == 501
    body = stub_resp.json()
    assert "detail" in body
    assert "D010" in body["detail"]


async def test_request_new_goal_type_unauthenticated_returns_401():
    """POST .../request-new-goal-type without auth returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            json={"prompt_summary": "irrelevant"},
        )
    assert resp.status_code == 401


async def test_request_new_goal_type_nonexistent_session_returns_404():
    """POST .../request-new-goal-type for a non-existent session returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/chat/sessions/{fake_id}/request-new-goal-type",
            json={"prompt_summary": "irrelevant"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404