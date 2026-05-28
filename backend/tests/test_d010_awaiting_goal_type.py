"""Tests for D010: awaiting_goal_type status, awaiting_direction_id column,
and related lifecycle behaviors."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.goal import Goal
from app.models.user import User


# ---------------------------------------------------------------------------
# Model-level persistence tests
# ---------------------------------------------------------------------------

async def test_awaiting_goal_type_status_persists():
    """Goal can be created and persisted with awaiting_goal_type status."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="awaiting-status@test.com",
            display_name="Awaiting Status Tester",
            auth_provider="google",
            auth_provider_id="google-awaiting-status",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        goal = Goal(
            user_id=user_id,
            title="Pushup Counter Goal",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="awaiting_goal_type",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id

    assert goal_id is not None
    assert isinstance(goal_id, uuid.UUID)
    await engine.dispose()


async def test_awaiting_direction_id_nullable():
    """awaiting_direction_id column accepts NULL and non-NULL string values."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="direction-id@test.com",
            display_name="Direction ID Tester",
            auth_provider="google",
            auth_provider_id="google-dir-id",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        # Goal with awaiting_direction_id set
        goal_with_dir = Goal(
            user_id=user_id,
            title="With Direction ID",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id="011-pushup-counter",
        )
        session.add(goal_with_dir)

        # Goal with awaiting_direction_id as NULL
        goal_null_dir = Goal(
            user_id=user_id,
            title="Null Direction ID",
            goal_type="youtube_video",
            pledge_amount=2000,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="draft",
            awaiting_direction_id=None,
        )
        session.add(goal_null_dir)
        await session.commit()

        dir_id = goal_with_dir.awaiting_direction_id
        null_id = goal_null_dir.awaiting_direction_id

    assert dir_id == "011-pushup-counter"
    assert null_id is None
    await engine.dispose()


async def test_awaiting_direction_id_reads_back_after_commit():
    """awaiting_direction_id survives a round-trip through the database."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="roundtrip@test.com",
            display_name="Roundtrip Tester",
            auth_provider="google",
            auth_provider_id="google-roundtrip",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        goal = Goal(
            user_id=user_id,
            title="Roundtrip Goal",
            goal_type="youtube_video",
            pledge_amount=1500,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id="047-pushup-counter-side-angle",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id

    # Re-read from a fresh session
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        reloaded = result.scalar_one()
        read_status = reloaded.status
        read_dir_id = reloaded.awaiting_direction_id

    assert read_status == "awaiting_goal_type"
    assert read_dir_id == "047-pushup-counter-side-angle"
    await engine.dispose()


# ---------------------------------------------------------------------------
# Enum validation tests
# ---------------------------------------------------------------------------

async def test_goal_status_enum_includes_awaiting_goal_type():
    """The goal_status PostgreSQL enum includes 'awaiting_goal_type'."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT unnest(enum_range(NULL::goal_status))"
        ))
        enum_values = {row[0] for row in result}

    assert "awaiting_goal_type" in enum_values, (
        f"awaiting_goal_type not found in goal_status enum: {enum_values}"
    )
    await engine.dispose()


async def test_notification_type_enum_includes_goal_type_ready():
    """The notification_type PostgreSQL enum includes 'goal_type_ready'."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT unnest(enum_range(NULL::notification_type))"
        ))
        enum_values = {row[0] for row in result}

    assert "goal_type_ready" in enum_values, (
        f"goal_type_ready not found in notification_type enum: {enum_values}"
    )
    await engine.dispose()


# ---------------------------------------------------------------------------
# Deadline-worker skip test
# ---------------------------------------------------------------------------

async def test_deadline_worker_skips_awaiting_goal_type_goals():
    """check_deadlines does not charge or transition awaiting_goal_type goals."""
    from app.workers.deadline import check_deadlines

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="deadline-skip@test.com",
            display_name="Deadline Skip Tester",
            auth_provider="google",
            auth_provider_id="google-deadline-skip",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        # Create an awaiting_goal_type goal with a past deadline
        past_deadline = datetime.now(timezone.utc) - timedelta(days=2)
        goal = Goal(
            user_id=user_id,
            title="Past Deadline Awaiting Goal",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=past_deadline,
            status="awaiting_goal_type",
            awaiting_direction_id="011-pushup-counter",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id

    # Run the deadline checker
    await check_deadlines()

    # Verify the goal is still in awaiting_goal_type (not failed)
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        goal_after = result.scalar_one()
        status_after = goal_after.status

    assert status_after == "awaiting_goal_type", (
        f"Expected awaiting_goal_type, got {status_after}"
    )
    await engine.dispose()


# ---------------------------------------------------------------------------
# Existing lifecycle regression tests
# ---------------------------------------------------------------------------

async def test_existing_status_transitions_still_work():
    """Goal can still transition through the standard lifecycle: draft -> active -> cancelled."""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="lifecycle-regression@test.com",
            display_name="Lifecycle Regression Tester",
            auth_provider="google",
            auth_provider_id="google-lifecycle",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        goal = Goal(
            user_id=user_id,
            title="Lifecycle Regression Goal",
            goal_type="youtube_video",
            pledge_amount=5000,
            deadline=datetime.now(timezone.utc) + timedelta(days=7),
            status="draft",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id

    # draft -> active
    async with async_session() as session:
        await session.execute(
            text("UPDATE goals SET status = 'active' WHERE id = :id"),
            {"id": goal_id},
        )
        await session.commit()

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        goal_after = result.scalar_one()
        assert goal_after.status == "active"

    # active -> cancelled
    async with async_session() as session:
        await session.execute(
            text("UPDATE goals SET status = 'cancelled' WHERE id = :id"),
            {"id": goal_id},
        )
        await session.commit()

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        goal_after = result.scalar_one()
        assert goal_after.status == "cancelled"

    await engine.dispose()