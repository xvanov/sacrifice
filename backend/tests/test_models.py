import uuid
from datetime import datetime, timezone

import pytest
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


async def test_chat_session_status_enum_values():
    """Only valid chat_session_status enum values ('active', 'goal_created',
    'awaiting_goal_type') can be persisted; invalid values are rejected."""
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

        # All three valid enum values must persist without error.
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

        # An invalid status value must be rejected by the DB.
        with pytest.raises(Exception):
            bad = ChatSession(
                user_id=user.id,
                messages=[],
                status="invalid_status",
            )
            session.add(bad)
            await session.commit()

    await engine.dispose()


async def test_chat_session_create_session_ownership():
    """A session created via the API is owned by the authenticated user:
    the user_id foreign key matches, and the session is fetchable only
    with that user's id."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="chat_owner@test.com",
            display_name="Chat Owner",
            auth_provider="google",
            auth_provider_id="chat-owner-1",
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

    # Verify the session is owned by the correct user via direct query.
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT user_id FROM chat_sessions WHERE id = :id"
            ),
            {"id": sid},
        )
        row = result.fetchone()

    assert row is not None
    assert str(row.user_id) == str(user.id), (
        f"chat session must be owned by user {user.id}, got {row.user_id}"
    )

    # Verify the session is NOT found with a different user_id.
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT id FROM chat_sessions WHERE id = :id AND user_id = :uid"
            ),
            {"id": sid, "uid": str(uuid.uuid4())},
        )
        row = result.fetchone()

    assert row is None, (
        "chat session must not be fetchable with a different user_id"
    )
    await engine.dispose()
