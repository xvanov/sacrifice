import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from app.config import settings
from app.main import app  # noqa: F811 — used by ASGITransport in make_client
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine

GREETING_MESSAGE = {
    "role": "assistant",
    "content": "Tell me what you want to do, and I'll figure out how to track it.",
    "action": None,
}

CHAT_MIGRATION_REV = "e22b7086c9bd"
CHAT_MIGRATION_PARENT = "9d4f2a6e1c70"

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _quote_ident(name: str) -> str:
    """Return a properly quoted PostgreSQL identifier."""
    return f'"{name}"'


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client, email="test@example.com", name="Test User", sub="test-sub-123", token="valid-token"
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


def _run_alembic(command: str, revision: str, env: dict[str, str] | None = None) -> None:
    """Run an Alembic CLI command in a subprocess to avoid event-loop
    conflicts with pytest-asyncio (env.py calls asyncio.run at import).

    An optional *env* dict augments the subprocess environment, e.g. to
    point alembic at an isolated test database.
    """
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        env=run_env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {command} {revision} failed (rc={result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def _isolated_migration_env() -> tuple[dict[str, str], str, str]:
    """Return an env dict pointing at a throwaway database.

    The caller must CREATE and DROP the database; this just computes the
    name and the matching ``DATABASE_URL`` override using SQLAlchemy URL
    parsing so the test is not coupled to the shape of the configured URL.
    """
    parsed = make_url(settings.database_url)
    isolated_name = f"sacrifice_test_migration_{uuid.uuid4().hex[:12]}"
    isolated_url = parsed.set(database=isolated_name).render_as_string(
        hide_password=False,
    )
    return {"DATABASE_URL": isolated_url}, isolated_name, isolated_url


def _admin_url() -> str:
    """Return a connection URL for the default ``postgres`` maintenance
    database derived from the configured database URL."""
    return (
        make_url(settings.database_url)
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )


# ---------------------------------------------------------------------------
# Test 1: create_chat_session_returns_greeting_and_active_status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
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
    assert isinstance(body["session_id"], str)
    # session_id must parse as a valid UUID
    parsed = uuid.UUID(body["session_id"])
    assert str(parsed) == body["session_id"]

    assert body["messages"] == [GREETING_MESSAGE]
    assert body["status"] == "active"


# ---------------------------------------------------------------------------
# Test 2: create_chat_session_persists_session_record_with_expected_defaults
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_chat_session_persists_session_record_with_expected_defaults():
    """Session created via API is persisted with correct row defaults
    including non-null created_at and updated_at timestamps."""
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
                    "SELECT id, user_id, status, messages, draft_goal, "
                    "       created_at, updated_at "
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
    assert row.created_at is not None, "created_at must be persisted"
    assert row.updated_at is not None, "updated_at must be persisted"


# ---------------------------------------------------------------------------
# Test 3: create_chat_session_requires_authentication
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_chat_session_requires_authentication():
    """Unauthenticated POST to /api/chat/sessions returns 401."""
    async with make_client() as client:
        response = await client.post("/api/chat/sessions")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test 4: chat_sessions_migration_creates_required_columns_and_types
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_sessions_migration_creates_required_columns_and_types():
    """Run the chat_sessions Alembic migration against an *isolated* throwaway
    database, then assert the resulting schema has all required columns with
    correct types and enum values without mutating the shared test database."""

    env_override, isolated_name, isolated_url = _isolated_migration_env()

    # Create the isolated database by connecting to the default 'postgres' db.
    admin_engine = create_async_engine(_admin_url(), echo=False)
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text("COMMIT"))
            await conn.execute(text(f"CREATE DATABASE {_quote_ident(isolated_name)}"))
    finally:
        await admin_engine.dispose()

    try:
        # Run the full migration history on the isolated database.
        _run_alembic("upgrade", "head", env=env_override)

        # Now inspect the isolated database.
        inspect_engine = create_async_engine(isolated_url, echo=False)
        try:
            async with inspect_engine.connect() as conn:
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
                assert table_exists, "chat_sessions table must exist after upgrade"

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
                        f"column '{col_name}' type: expected {expected_type}, got {col.data_type}"
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
            await inspect_engine.dispose()
    finally:
        # Drop the isolated database using a fresh admin engine since the
        # original admin_engine was already disposed above.
        drop_engine = create_async_engine(_admin_url(), echo=False)
        try:
            async with drop_engine.connect() as conn:
                await conn.execute(text("COMMIT"))
                await conn.execute(text(f"DROP DATABASE IF EXISTS {_quote_ident(isolated_name)}"))
        finally:
            await drop_engine.dispose()


# ---------------------------------------------------------------------------
# Test 5: chat_router_is_registered_under_api_namespace
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_router_is_registered_under_api_namespace():
    """POST /api/chat/sessions on the live ASGI app returns a
    contract-defined status (401 for unauthenticated), proving the
    endpoint is actually served — not just present in route metadata."""
    async with make_client() as client:
        response = await client.post("/api/chat/sessions")

    assert response.status_code == 401, (
        "unauthenticated POST to /api/chat/sessions must return 401, "
        "proving the route is live and enforcing auth"
    )
