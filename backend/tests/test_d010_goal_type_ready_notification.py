"""Tests for D010 goal_type_ready notification.

Covers:
- goal_type_ready added to NotificationType enum
- Notification fired when a generation's PR is merged (status → pr_merged)
- Notification links to the correct goal
- Only fires once (not on repeated polling)
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.user import User


# ─── NotificationType enum ──────────────────────────────────────────


async def test_notification_type_includes_goal_type_ready():
    """The NotificationType enum includes 'goal_type_ready'."""
    from app.models.notification import NotificationType

    assert hasattr(NotificationType, "goal_type_ready")
    assert NotificationType.goal_type_ready.value == "goal_type_ready"


# ─── Notification creation on pr_merged ────────────────────────────


async def test_fire_goal_type_ready_notification_creates_notification():
    """fire_goal_type_ready_notification() inserts a Notification row."""
    from app.services.notification import get_user_notifications
    from app.services.goal_type_ready_notification import fire_goal_type_ready_notification

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="notif-user@test.com",
            display_name="Notification User",
            auth_provider="google",
            auth_provider_id="google-notif-user",
        )
        session.add(user)
        await session.commit()

        await fire_goal_type_ready_notification(
            db=session,
            user_id=user.id,
            goal_id=uuid.uuid4(),
            direction_id="011-pushup-counter",
            goal_title="20 morning pushups",
        )

    async with async_session() as session:
        notifications = await get_user_notifications(session, user.id, limit=10)

    assert len(notifications) >= 1
    notif = notifications[0]
    assert notif.type == "goal_type_ready"
    assert "pushup-counter" in notif.title.lower()
    assert notif.read is False
    await engine.dispose()


async def test_goal_type_ready_notification_references_goal():
    """The goal_type_ready notification links to the correct goal_id."""
    from app.services.goal_type_ready_notification import fire_goal_type_ready_notification

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    goal_id = uuid.uuid4()
    async with async_session() as session:
        user = User(
            email="goal-link@test.com",
            display_name="Goal Link User",
            auth_provider="google",
            auth_provider_id="google-goal-link",
        )
        session.add(user)
        await session.commit()

        await fire_goal_type_ready_notification(
            db=session,
            user_id=user.id,
            goal_id=goal_id,
            direction_id="011-pushup-counter",
            goal_title="Pushups",
        )

    async with async_session() as session:
        from app.models.notification import Notification
        result = await session.execute(
            select(Notification).where(Notification.goal_id == goal_id)
        )
        notifs = list(result.scalars().all())

    assert len(notifs) >= 1
    assert all(n.goal_id == goal_id for n in notifs)
    await engine.dispose()


# ─── Idempotency tests ──────────────────────────────────────────────


async def test_goal_type_ready_notification_not_duplicated_on_repeated_poll():
    """Polling pr_merged status multiple times does not create duplicate notifications."""
    from app.services.goal_type_ready_notification import fire_goal_type_ready_notification
    from app.services.notification import get_user_notifications

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    goal_id = uuid.uuid4()
    async with async_session() as session:
        user = User(
            email="idempotent-notif@test.com",
            display_name="Idempotent User",
            auth_provider="google",
            auth_provider_id="google-idempotent",
        )
        session.add(user)
        await session.commit()

        # Fire twice with the same direction_id
        for _ in range(2):
            await fire_goal_type_ready_notification(
                db=session,
                user_id=user.id,
                goal_id=goal_id,
                direction_id="011-pushup-counter",
                goal_title="Pushups",
            )

    async with async_session() as session:
        notifications = await get_user_notifications(session, user.id, limit=10)

    # Should only create one notification for this direction/goal combo
    matching = [n for n in notifications if n.goal_id == goal_id]
    assert len(matching) == 1
    await engine.dispose()


async def test_different_goals_get_separate_notifications():
    """Two different goals pending generation each get their own notification."""
    from app.services.goal_type_ready_notification import fire_goal_type_ready_notification
    from app.services.notification import get_user_notifications

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    goal_a_id = uuid.uuid4()
    goal_b_id = uuid.uuid4()
    async with async_session() as session:
        user = User(
            email="multi-goal@test.com",
            display_name="Multi-Goal User",
            auth_provider="google",
            auth_provider_id="google-multi-goal",
        )
        session.add(user)
        await session.commit()

        await fire_goal_type_ready_notification(
            db=session, user_id=user.id, goal_id=goal_a_id,
            direction_id="011-pushup-counter", goal_title="Pushups",
        )
        await fire_goal_type_ready_notification(
            db=session, user_id=user.id, goal_id=goal_b_id,
            direction_id="012-squat-tracker", goal_title="Squats",
        )

    async with async_session() as session:
        notifications = await get_user_notifications(session, user.id, limit=10)

    matching = [n for n in notifications if n.type == "goal_type_ready"]
    assert len(matching) == 2
    goal_ids = {n.goal_id for n in matching}
    assert goal_a_id in goal_ids
    assert goal_b_id in goal_ids
    await engine.dispose()


# ─── Integration: generation-status polling triggers notification ───


async def test_generation_status_pr_merged_triggers_notification():
    """When generation-status endpoint observes pr_merged, a goal_type_ready notification fires."""
    from app.services.notification import get_user_notifications

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="poll-trigger@test.com",
            display_name="Poll Trigger",
            auth_provider="google",
            auth_provider_id="google-poll-trigger",
        )
        session.add(user)
        await session.commit()

        # Simulate: a goal is in awaiting_goal_type, the direction just hit pr_merged
        from app.services.generation_status import check_and_notify_if_merged

        with patch.object(check_and_notify_if_merged, "__defaults__", None):
            await check_and_notify_if_merged(
                db=session,
                user_id=user.id,
                goal_id=uuid.uuid4(),
                direction_id="011-pushup-counter",
                session_id="test-session",
                goal_title="20 morning pushups",
            )

    async with async_session() as session:
        notifications = await get_user_notifications(session, user.id, limit=10)

    assert len(notifications) >= 1
    notif = notifications[0]
    assert notif.type == "goal_type_ready"
    await engine.dispose()