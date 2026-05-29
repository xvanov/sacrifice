"""Tests for the D009 chat-sessions slice: persistence + route skeleton
+ create-session endpoint.

These tests verify the child-story scope of D009. They do NOT test match
behavior, message-turn processing, or create-goal handoff.
"""

import os
import subprocess
import sys
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app

CHAT_SESSIONS_FQN = "chat_sessions"

GREETING = "Tell me what you want to do, and I'll figure out how to track it."


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


def _db_engine(db_url: str | None = None):
    url = db_url or settings.database_url
    return create_async_engine(url, echo=False)


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


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/sessions
# ═══════════════════════════════════════════════════════════════════════════


async def test_create_chat_session_returns_201_with_session_id_and_messages():
    """POST /api/chat/sessions returns 201 with a valid UUID session_id,
    messages list, and status='active'. The messages list contains exactly
    one message: the assistant greeting specified in api_spec.md."""
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 201
    body = response.json()
    assert "session_id" in body
    # session_id must be a valid UUID
    try:
        parsed = uuid.UUID(body["session_id"])
    except (ValueError, TypeError):
        pytest.fail(f"session_id is not a valid UUID: {body['session_id']!r}")
    assert parsed.version is not None
    assert body["status"] == "active"
    assert "messages" in body
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) == 1
    msg = body["messages"][0]
    assert msg["role"] == "assistant"
    assert msg["content"] == GREETING
    assert msg["action"] is None


async def test_create_chat_session_unauthenticated_returns_401():
    """POST /api/chat/sessions without an Authorization header returns 401."""
    async with make_client() as client:
        response = await client.post("/api/chat/sessions")
    assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Persistence (direct-DB verification)
# ═══════════════════════════════════════════════════════════════════════════


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
    assert msg["content"] == GREETING
    assert msg["action"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Router registration
# ═══════════════════════════════════════════════════════════════════════════


async def test_chat_router_is_mounted_on_app():
    """The chat router is registered in main.py so POST /api/chat/sessions
    responds to requests. A 401 proves the route is mounted (it just lacks
    credentials) rather than returning a 404 Not Found."""
    async with make_client() as client:
        response = await client.post("/api/chat/sessions")
    # Mounted route with auth guard → 401; unmounted → 404
    assert response.status_code == 401, (
        f"Expected 401 (mounted, no auth) but got {response.status_code}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/sessions/{session_id}/request-new-goal-type  (STUB)
# ═══════════════════════════════════════════════════════════════════════════


async def test_request_new_goal_type_returns_501_stub():
    """The request-new-goal-type endpoint is stubbed per D009 scope and
    returns 501 with the D010 supersedes message."""
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        session_id = create_resp.json()["session_id"]

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "Track 8 glasses of water per day"},
        )
    assert resp.status_code == 501
    body = resp.json()
    assert body["detail"] == "Goal-type generation is delivered in D010"


async def test_request_new_goal_type_unauthenticated_returns_401():
    """The stubbed endpoint requires authentication."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 401


async def test_request_new_goal_type_session_not_found_returns_404():
    """A valid UUID that doesn't correspond to any session returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.post(
            f"/api/chat/sessions/{fake_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 404


async def test_request_new_goal_type_not_owned_by_user_returns_403():
    """Per api_spec.md, accessing a session that exists but belongs to a
    different user must return 403 (not 404)."""
    async with make_client() as client:
        # User A creates a session
        token_a, _ = await _auth(client)
        create_resp = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        session_id = create_resp.json()["session_id"]

        # User B tries to access user A's session
        token_b, _ = await _auth(
            client,
            email="other@example.com",
            name="Other",
            sub="other-sub",
            token="other-token",
        )
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 403


async def test_request_new_goal_type_invalid_uuid_returns_404():
    """A non-UUID session_id path segment returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/not-a-uuid/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "test"},
        )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Migration verification — schema contract + upgrade/downgrade
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_chat_sessions_migration_schema_contract_and_downgrade():
    """Verify the chat_sessions table schema contract (required columns,
    JSONB types, status enum constraint) after upgrade, then downgrade and
    confirm table removal. Runs against an isolated temporary database."""
    base_url = settings.database_url
    if "/" not in base_url:
        pytest.skip("Cannot parse database_url for temp DB creation")

    parts = base_url.rsplit("/", 1)
    server_part = parts[0]
    db_name = parts[1].split("?")[0]

    temp_db_name = f"{db_name}_migration_test"
    temp_db_url = f"{server_part}/{temp_db_name}"

    # Build a sync-compatible URL for CREATE/DROP DATABASE via asyncpg
    sync_server = server_part.replace("+asyncpg", "", 1)
    postgres_url = f"{sync_server}/postgres"

    # 1. Create temp database via asyncpg (already a project dependency)
    try:
        import asyncpg as apg
    except ImportError:
        pytest.skip("asyncpg not available for temp DB migration test")

    apg_conn = await apg.connect(postgres_url)
    try:
        await apg_conn.execute(f"DROP DATABASE IF EXISTS {temp_db_name}")
        await apg_conn.execute(f"CREATE DATABASE {temp_db_name}")
    finally:
        await apg_conn.close()

    try:
        # 2. Run alembic upgrade head against the temp DB
        #    cwd is backend/ — the directory containing alembic.ini
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["DATABASE_URL"] = temp_db_url
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=cwd, capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, (
            f"alembic upgrade failed on temp DB: {result.stderr}"
        )

        # 3. Verify schema contract on upgraded temp DB
        temp_engine = _db_engine(temp_db_url)
        try:
            async with temp_engine.connect() as tconn:
                # 3a. Table exists
                result = await tconn.execute(text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = :tbl)"
                ), {"tbl": CHAT_SESSIONS_FQN})
                assert result.scalar(), (
                    "chat_sessions must exist after upgrade on temp DB"
                )

                # 3b. Required columns with correct types
                result = await tconn.execute(text("""
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_name = :tbl
                    ORDER BY ordinal_position
                """), {"tbl": CHAT_SESSIONS_FQN})
                columns = {row.column_name: row for row in result.mappings()}

                required = {
                    "id": "uuid",
                    "created_at": "timestamp with time zone",
                    "updated_at": "timestamp with time zone",
                    "user_id": "uuid",
                    "messages": "jsonb",
                    "draft_goal": "jsonb",
                    "status": "USER-DEFINED",
                }
                for col_name, col_type in required.items():
                    assert col_name in columns, (
                        f"Missing required column: {col_name}"
                    )
                    assert columns[col_name].data_type == col_type, (
                        f"Column {col_name}: expected {col_type}, "
                        f"got {columns[col_name].data_type}"
                    )

                # 3c. messages is NOT NULL
                assert columns["messages"].is_nullable == "NO", (
                    "messages column must be NOT NULL"
                )

                # 3d. draft_goal is nullable
                assert columns["draft_goal"].is_nullable == "YES", (
                    "draft_goal column must be nullable"
                )

                # 3e. Foreign key to users table
                result = await tconn.execute(text("""
                    SELECT ccu.table_name AS referenced_table
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.constraint_column_usage ccu
                      ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = :tbl
                """), {"tbl": CHAT_SESSIONS_FQN})
                fk_tables = [row.referenced_table for row in result.mappings()]
                assert "users" in fk_tables, (
                    "chat_sessions must have FK to users table"
                )
        finally:
            await temp_engine.dispose()

        # 4. Verify status enum constraint — valid values accepted,
        #    invalid values rejected
        engine2 = _db_engine(temp_db_url)
        try:
            async with engine2.connect() as tconn:
                # Need a user row for the FK
                user_id = "00000000-0000-0000-0000-000000000001"
                await tconn.execute(text(
                    "INSERT INTO users (id, email, display_name, auth_provider, "
                    "auth_provider_id) "
                    "VALUES (:id, 'migtest@test.com', 'Mig Test', 'google', "
                    "'migtest-sub')"
                ), {"id": user_id})
                await tconn.commit()

                # Valid status values from the enum
                for s in ("active", "goal_created", "awaiting_goal_type"):
                    sid = str(uuid.uuid4())
                    await tconn.execute(text(
                        "INSERT INTO chat_sessions (id, user_id, messages, status) "
                        "VALUES (:id, :uid, '[]'::jsonb, :status)"
                    ), {"id": sid, "uid": user_id, "status": s})
                await tconn.commit()

                # Invalid status must be rejected
                with pytest.raises(Exception):
                    sid = str(uuid.uuid4())
                    await tconn.execute(text(
                        "INSERT INTO chat_sessions (id, user_id, messages, status) "
                        "VALUES (:id, :uid, '[]'::jsonb, 'invalid_status')"
                    ), {"id": sid, "uid": user_id, "status": "invalid_status"})
                    await tconn.commit()
                await tconn.rollback()
        finally:
            await engine2.dispose()

        # 5. Downgrade one step
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "-1"],
            cwd=cwd, capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, (
            f"alembic downgrade failed on temp DB: {result.stderr}"
        )

        # 6. Verify chat_sessions table is gone
        temp_engine3 = _db_engine(temp_db_url)
        try:
            async with temp_engine3.connect() as tconn:
                result = await tconn.execute(text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = :tbl)"
                ), {"tbl": CHAT_SESSIONS_FQN})
                assert not result.scalar(), (
                    "chat_sessions must NOT exist after downgrade on temp DB"
                )
        finally:
            await temp_engine3.dispose()

    finally:
        # 7. Drop temp database via asyncpg
        apg_conn2 = await apg.connect(postgres_url)
        try:
            await apg_conn2.execute(f"DROP DATABASE IF EXISTS {temp_db_name}")
        finally:
            await apg_conn2.close()