import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.chat_session import ChatSession
from app.models.goal import Goal
from app.models.user import User

GREETING_MESSAGE = {
    "role": "assistant",
    "content": "Tell me what you want to do, and I'll figure out how to track it.",
    "action": None,
}


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


# ---------------------------------------------------------------------------
# ChatSession model tests
# ---------------------------------------------------------------------------


async def test_chat_session_status_enum_values():
    """All three allowed chat_session_status enum values ('active',
    'goal_created', 'awaiting_goal_type') persist and read back correctly."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="chat_enum@test.com",
            display_name="Chat Enum Tester",
            auth_provider="google",
            auth_provider_id="chat-enum-1",
        )
        session.add(user)
        await session.commit()

        for status_val in ("active", "goal_created", "awaiting_goal_type"):
            session_obj = ChatSession(
                user_id=user.id,
                messages=[],
                status=status_val,
            )
            session.add(session_obj)
            await session.commit()
            await session.refresh(session_obj)
            assert session_obj.status == status_val, (
                f"Expected status '{status_val}', got '{session_obj.status}'"
            )

    await engine.dispose()

