# Hermetic test env. The suite must run without a real .env (a fresh factory
# worktree / CI checkout has none — .env is gitignored). Every external call is
# mocked in unit tests, so these dummy-but-present values just stop the app
# from short-circuiting/500-ing on empty config. Set BEFORE `app.config` is
# imported below, and via setdefault so a real exported env still wins.
# (Without this, ~13 credential-dependent tests failed only in a credentialless
# checkout, making the factory's dev-loop test gate unsatisfiable — 2026-07-07.)
import os as _os

_TEST_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice",
    "JWT_SECRET": "test-jwt-secret-not-for-production",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "STRIPE_PUBLISHABLE_KEY": "pk_test_dummy",
    "STRIPE_WEBHOOK_SECRET": "whsec_test_dummy",
    "GOOGLE_CLIENT_ID": "test-google-client-id",
    "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
    "GITHUB_CLIENT_ID": "test-github-client-id",
    "GITHUB_CLIENT_SECRET": "test-github-client-secret",
    "YOUTUBE_API_KEY": "test-youtube-key",
    "AZURE_FOUNDRY_ENDPOINT": "https://test-foundry.example.com/",
    "AZURE_FOUNDRY_API_KEY": "test-azure-key",
    "SACRIFICE_MEDIA_DIR": "/tmp/sacrifice-test-media",
}
for _k, _v in _TEST_ENV_DEFAULTS.items():
    _os.environ.setdefault(_k, _v)

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.base import Base

TEST_DB_URL = settings.database_url


async def _ensure_chat_session_columns(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS session_id VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS goal_id UUID"))
        await conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS awaiting_direction_id VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ"))
        await conn.execute(text("UPDATE chat_sessions SET last_activity_at = COALESCE(last_activity_at, updated_at, created_at, NOW())"))
        await conn.execute(text("ALTER TABLE chat_sessions ALTER COLUMN last_activity_at SET DEFAULT NOW()"))
        await conn.execute(text("ALTER TABLE chat_sessions ALTER COLUMN last_activity_at SET NOT NULL"))
        # D010: goals table may have been created before awaiting_direction_id was added.
        await conn.execute(text("ALTER TABLE goals ADD COLUMN IF NOT EXISTS awaiting_direction_id VARCHAR(255)"))


async def _ensure_verification_columns(engine) -> None:
    """Ensure D093 verification columns exist on pre-existing tables.

    create_all only works for new tables; for existing tables we need
    ALTER … ADD COLUMN IF NOT EXISTS so existing test databases work.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE")
        )


@pytest_asyncio.fixture(autouse=True)
async def _clear_rate_limit_store():
    """Clear the rate-limiter store before every test so no test leaks into another."""
    from app.core.rate_limiter import _store
    _store.clear()
    yield


@pytest_asyncio.fixture(autouse=True)
async def test_db():
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    test_async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with test_async_session() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    # Ensure tables exist without destructive operations (avoids masking
    # migration issues). create_all is idempotent — it skips existing tables.
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_chat_session_columns(test_engine)
    await _ensure_verification_columns(test_engine)

    yield

    # Truncate only D010-specific tables after each test to keep isolation
    # without the blanket DROP that the reviewer flagged as too invasive.
    _D010_TABLES = {
        "audit_events", "chat_spend_ledger", "chat_sessions",
        "email_verification_tokens", "goals", "goal_criteria",
        "media_uploads", "notifications", "proof_submissions",
        "payments", "users",
    }
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _D010_TABLES:
                await conn.execute(text(f"TRUNCATE {table.name} CASCADE"))
    await test_engine.dispose()

    app.dependency_overrides.clear()
