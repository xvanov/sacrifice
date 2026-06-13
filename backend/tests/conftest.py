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

    yield

    # Truncate only D010-specific tables after each test to keep isolation
    # without the blanket DROP that the reviewer flagged as too invasive.
    _D010_TABLES = {
        "chat_spend_ledger", "chat_sessions", "goals", "goal_criteria",
        "media_uploads", "notifications", "proof_submissions", "payments", "users",
    }
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _D010_TABLES:
                await conn.execute(text(f"TRUNCATE {table.name} CASCADE"))
    await test_engine.dispose()

    app.dependency_overrides.clear()
