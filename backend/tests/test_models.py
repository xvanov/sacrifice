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


async def test_chat_session_creation_defaults():
    """ChatSession persisted with defaults: status='active', messages set,
    draft_goal=None, timestamps non-null."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="chat@test.com",
            display_name="Chat Tester",
            auth_provider="google",
            auth_provider_id="chat-789",
        )
        session.add(user)
        await session.commit()

        session_obj = ChatSession(
            user_id=user.id,
            messages=[GREETING_MESSAGE],
        )
        session.add(session_obj)
        await session.commit()
        sid = session_obj.id

        # Re-fetch to confirm persistence
        result = await session.execute(
            text("SELECT id, user_id, status, messages, draft_goal, created_at, updated_at FROM chat_sessions WHERE id = :id"),
            {"id": sid},
        )
        row = result.fetchone()

    assert row is not None
    assert row.status == "active"
    assert row.messages == [GREETING_MESSAGE]
    assert row.draft_goal is None
    assert row.created_at is not None
    assert row.updated_at is not None
    assert str(row.user_id) == str(user.id)
    await engine.dispose()


async def test_chat_session_explicit_status():
    """ChatSession can be created with explicit status."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="chat2@test.com",
            display_name="Chat Tester 2",
            auth_provider="google",
            auth_provider_id="chat-101",
        )
        session.add(user)
        await session.commit()

        session_obj = ChatSession(
            user_id=user.id,
            messages=[],
            status="goal_created",
            draft_goal={"title": "Test Goal"},
        )
        session.add(session_obj)
        await session.commit()

        result = await session.execute(
            text("SELECT status, draft_goal FROM chat_sessions WHERE id = :id"),
            {"id": session_obj.id},
        )
        row = result.fetchone()

    assert row.status == "goal_created"
    assert row.draft_goal == {"title": "Test Goal"}
    await engine.dispose()


async def test_chat_session_user_relationship():
    """ChatSession.user back-populates to the owning User."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="chat3@test.com",
            display_name="Chat Tester 3",
            auth_provider="google",
            auth_provider_id="chat-202",
        )
        session.add(user)
        await session.commit()

        session_obj = ChatSession(
            user_id=user.id,
            messages=[GREETING_MESSAGE],
        )
        session.add(session_obj)
        await session.commit()

        # Load user with chat_sessions eager-loaded
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        result = await session.execute(
            select(User).where(User.id == user.id).options(selectinload(User.chat_sessions))
        )
        loaded_user = result.scalar_one()

    assert len(loaded_user.chat_sessions) == 1
    assert loaded_user.chat_sessions[0].id == session_obj.id
    await engine.dispose()
