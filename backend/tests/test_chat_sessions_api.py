from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app

GREETING_MESSAGE = {
    "role": "assistant",
    "content": "Tell me what you want to do, and I'll figure out how to track it.",
    "action": None,
}


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


# ---------------------------------------------------------------------------
# Test 1: create_chat_session_returns_greeting_and_active_status
# ---------------------------------------------------------------------------
async def test_create_chat_session_returns_greeting_and_active_status():
    """POST /api/chat/sessions returns 201 with the exact greeting payload."""
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 201
    body = response.json()

    assert "session_id" in body
    # session_id must be a non-empty stringified UUID
    assert isinstance(body["session_id"], str)
    assert len(body["session_id"]) == 36
    assert body["session_id"].count("-") == 4

    assert body["messages"] == [GREETING_MESSAGE]
    assert body["status"] == "active"


# ---------------------------------------------------------------------------
# Test 2: create_chat_session_persists_session_record_with_expected_defaults
# ---------------------------------------------------------------------------
async def test_create_chat_session_persists_session_record_with_expected_defaults():
    """Session created via API is persisted with correct row defaults."""
    async with make_client() as client:
        token, user = await _auth(client)
        response = await client.post(
            "/api/chat/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = response.json()
        session_id = body["session_id"]

    # Query the DB directly through a fresh engine to confirm persistence.
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, user_id, status, messages, draft_goal "
                    "FROM chat_sessions WHERE id = :id"
                ),
                {"id": session_id},
            )
            row = result.fetchone()
    finally:
        await engine.dispose()

    assert row is not None, "session row must be persisted"
    assert str(row.user_id) == user["id"]
    assert row.status == "active"
    assert row.messages == [GREETING_MESSAGE]
    assert row.draft_goal is None


# ---------------------------------------------------------------------------
# Test 3: create_chat_session_requires_authentication
# ---------------------------------------------------------------------------
async def test_create_chat_session_requires_authentication():
    """Unauthenticated POST to /api/chat/sessions returns 401."""
    async with make_client() as client:
        response = await client.post("/api/chat/sessions")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test 4: chat_sessions_migration_creates_required_columns_and_types
# ---------------------------------------------------------------------------
async def test_chat_sessions_migration_creates_required_columns_and_types():
    """The chat_sessions table has all required columns with correct types."""
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.connect() as conn:

            # Verify chat_sessions table exists in information_schema
            result = await conn.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.tables "
                    "  WHERE table_name = 'chat_sessions'"
                    ")"
                )
            )
            table_exists = result.scalar()
            assert table_exists, "chat_sessions table must exist"

            # Verify all required columns with correct data types
            result = await conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'chat_sessions' "
                    "ORDER BY ordinal_position"
                )
            )
            columns = {row.column_name: row for row in result.fetchall()}

            required = {
                "id": ("uuid", "NO"),
                "user_id": ("uuid", "NO"),
                "created_at": ("timestamp with time zone", "NO"),
                "updated_at": ("timestamp with time zone", "NO"),
                "messages": ("jsonb", "NO"),
                "draft_goal": ("jsonb", "YES"),
                "status": ("USER-DEFINED", "NO"),
            }

            for col_name, (expected_type, expected_nullable) in required.items():
                assert col_name in columns, f"column '{col_name}' must exist"
                col = columns[col_name]
                assert col.data_type == expected_type, (
                    f"column '{col_name}' type: expected {expected_type}, "
                    f"got {col.data_type}"
                )
                assert col.is_nullable == expected_nullable, (
                    f"column '{col_name}' nullable: expected {expected_nullable}, "
                    f"got {col.is_nullable}"
                )

            # Verify the status enum has only the expected values
            result = await conn.execute(
                text(
                    "SELECT e.enumlabel "
                    "FROM pg_enum e "
                    "JOIN pg_type t ON e.enumtypid = t.oid "
                    "WHERE t.typname = 'chat_session_status' "
                    "ORDER BY e.enumsortorder"
                )
            )
            enum_values = [row[0] for row in result.fetchall()]
            assert set(enum_values) == {"active", "goal_created", "awaiting_goal_type"}, (
                f"chat_session_status enum values: {enum_values}"
            )

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 5: chat_router_is_registered_under_api_namespace
# ---------------------------------------------------------------------------
async def test_chat_router_is_registered_under_api_namespace():
    """POST /api/chat/sessions is reachable and returns 401 (not 404) when
    unauthenticated, proving the router is mounted under /api."""
    async with make_client() as client:
        response = await client.post("/api/chat/sessions")

    # 401 means the route exists and the auth dependency fired.
    # 404 would mean the router is not registered.
    assert response.status_code == 401, (
        f"Expected 401 (route exists, auth required), got {response.status_code}. "
        "A 404 means the chat router is not registered under /api."
    )