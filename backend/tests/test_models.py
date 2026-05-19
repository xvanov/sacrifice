import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.goal import Goal
from app.models.user import User


async def test_create_user():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="test@example.com",
            display_name="Test User",
            auth_provider="google",
            auth_provider_id="google-123",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    assert user_id is not None
    assert isinstance(user_id, uuid.UUID)
    await engine.dispose()


async def test_goal_creation():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="goal@test.com",
            display_name="Goal Tester",
            auth_provider="github",
            auth_provider_id="gh-456",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        from datetime import datetime, timezone

        goal = Goal(
            user_id=user_id,
            title="Build Sacrifice API",
            goal_type="api_endpoint",
            pledge_amount=5000,
            deadline=datetime.now(timezone.utc),
            status="active",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id

    assert user_id is not None
    assert goal_id is not None
    assert isinstance(goal_id, uuid.UUID)
    await engine.dispose()
