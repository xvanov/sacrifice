"""Tests for the D009 chat-sessions slice: persistence + route skeleton
+ create-session endpoint.

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


# ── Migration verification ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_sessions_migration_downgrade_removes_table():
    """Verify the chat_sessions migration runs and downgrades cleanly against
    an isolated temporary database, leaving the shared test DB untouched."""
    import subprocess
    import sys
    import os

    # Derive a temporary database name from the configured database URL.
    # settings.database_url is e.g. postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice
    base_url = settings.database_url
    # Extract the server part and the database name
    # pattern: driver://user:pass@host:port/dbname
    if "/" not in base_url:
        pytest.skip("Cannot parse database_url for temp DB creation")

    parts = base_url.rsplit("/", 1)
    server_part = parts[0]  # e.g. postgresql+asyncpg://postgres:postgres@localhost:5433
    db_name = parts[1].split("?")[0]  # sacrifice (strip query params)

    temp_db_name = f"{db_name}_migration_test"
    temp_db_url = f"{server_part}/{temp_db_name}"

    # Use a synchronous psycopg2 connection for CREATE/DROP DATABASE.
    # Strip the async driver and use plain postgresql:// scheme.
    sync_server = server_part.replace("+asyncpg", "", 1)
    postgres_url = f"{sync_server}/postgres"

    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not available for temp DB migration test")

    # 1. Create the temp database
    conn = psycopg2.connect(postgres_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {temp_db_name}")
        cur.execute(f"CREATE DATABASE {temp_db_name}")
    conn.close()

    try:
        # 2. Run alembic upgrade head against the temp DB
        env = os.environ.copy()
        env["DATABASE_URL"] = temp_db_url
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=".", capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, (
            f"alembic upgrade failed on temp DB: {result.stderr}"
        )

        # 3. Verify chat_sessions table exists in temp DB
        temp_engine = _db_engine(temp_db_url)
        try:
            async with temp_engine.connect() as tconn:
                result = await tconn.execute(text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = :tbl)"
                ), {"tbl": CHAT_SESSIONS_FQN})
                assert result.scalar(), (
                    "chat_sessions must exist after upgrade on temp DB"
                )
        finally:
            await temp_engine.dispose()

        # 4. Downgrade one step
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "-1"],
            cwd=".", capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, (
            f"alembic downgrade failed on temp DB: {result.stderr}"
        )

        # 5. Verify chat_sessions table is gone
        temp_engine2 = _db_engine(temp_db_url)
        try:
            async with temp_engine2.connect() as tconn:
                result = await tconn.execute(text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = :tbl)"
                ), {"tbl": CHAT_SESSIONS_FQN})
                assert not result.scalar(), (
                    "chat_sessions must NOT exist after downgrade on temp DB"
                )
        finally:
            await temp_engine2.dispose()

    finally:
        # 6. Drop temp database
        conn = psycopg2.connect(postgres_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {temp_db_name}")
        conn.close()