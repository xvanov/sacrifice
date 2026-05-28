import os
import tempfile

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.base import Base

TEST_DB_URL = settings.database_url


@pytest_asyncio.fixture(autouse=True)
async def test_db():
    """Set up test database and override media_dir to a temp directory."""
    tmpdir = tempfile.mkdtemp()
    original_media_dir = settings.media_dir
    settings.media_dir = tmpdir

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

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE {table.name} CASCADE"))
    await test_engine.dispose()

    app.dependency_overrides.clear()
    settings.media_dir = original_media_dir
