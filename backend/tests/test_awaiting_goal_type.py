"""Tests for awaiting_goal_type status, direction linkage, and related behaviors.

These tests assert on production code that does NOT exist yet. Every test
in this file MUST fail (RED) on first run against the current codebase.

Covers:
- Model: awaiting_goal_type in Goal.status enum
- Model: nullable awaiting_direction_id column on goals
- Schema: awaiting_goal_type in GoalCreate/GoalUpdate/GoalResponse
- Service: ALLOWED_TRANSITIONS for awaiting_goal_type
- Worker: deadline worker skips awaiting_goal_type goals
- Notification: goal_type_ready notification type
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.goal import Goal
from app.models.notification import Notification


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


VALID_GOAL = {
    "title": "Ship the MVP",
    "description": "Launch the sacrifice app",
    "deadline": "2026-06-01T00:00:00Z",
    "pledge_amount": 5000,
    "goal_type": "youtube_video",
    "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
    "charity_id": "acct_charity123",
}


# ─── Model-layer: awaiting_goal_type status enum ──────────────────────


async def test_goal_model_accepts_awaiting_goal_type_status():
    """Goal model must accept 'awaiting_goal_type' as a valid status value."""
    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="awaiting@test.com",
            display_name="Awaiting Tester",
            auth_provider="google",
            auth_provider_id="google-awaiting-1",
        )
        session.add(user)
        await session.commit()

        goal = Goal(
            user_id=user.id,
            title="Awaiting goal type test",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id="011-pushup-counter",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id

    assert goal_id is not None
    assert isinstance(goal_id, uuid.UUID)

    # Re-read and verify status persisted correctly
    async with async_session() as session:
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        persisted = result.scalar_one()
        assert persisted.status == "awaiting_goal_type"

    await engine.dispose()


async def test_goal_model_nullable_awaiting_direction_id():
    """awaiting_direction_id must accept NULL (goal not tied to a direction)."""
    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="nullable@test.com",
            display_name="Nullable Tester",
            auth_provider="google",
            auth_provider_id="google-nullable-1",
        )
        session.add(user)
        await session.commit()

        goal = Goal(
            user_id=user.id,
            title="No direction linked",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id=None,
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id

    async with async_session() as session:
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        persisted = result.scalar_one()
        assert persisted.awaiting_direction_id is None

    await engine.dispose()


# ─── Schema-layer: awaiting_goal_type in schemas ────────────────────


async def test_goal_update_schema_accepts_awaiting_goal_type_status():
    """GoalUpdate schema must accept 'awaiting_goal_type' as a valid status."""
    from app.schemas.goal import GoalUpdate

    # The current GoalUpdate.validate_status does NOT include awaiting_goal_type.
    # This test MUST fail until the schema is updated.
    obj = GoalUpdate(status="awaiting_goal_type")
    assert obj.status == "awaiting_goal_type"


async def test_goal_response_includes_awaiting_direction_id():
    """GoalResponse must expose awaiting_direction_id when present."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = resp.json()["id"]

        # Set awaiting_direction_id directly via DB
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await db.execute(
                text("UPDATE goals SET awaiting_direction_id = :did WHERE id = :id"),
                {"did": "011-pushup-counter", "id": goal_id},
            )
            await db.commit()
        await engine.dispose()

        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "awaiting_direction_id" in body
        assert body["awaiting_direction_id"] == "011-pushup-counter"


async def test_goal_response_awaiting_direction_id_null_when_unset():
    """GoalResponse awaiting_direction_id must be null for goals without one."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = resp.json()["id"]

        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "awaiting_direction_id" in body
        assert body["awaiting_direction_id"] is None


# ─── Service-layer: ALLOWED_TRANSITIONS ───────────────────────────────


async def test_awaiting_goal_type_transitions_to_active():
    """Goal in awaiting_goal_type must be able to transition to active."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = resp.json()["id"]

        # The create-goal endpoint currently hardcodes status="draft".
        # For this test to work, we need the goal in awaiting_goal_type.
        # We'll set it directly via DB, then test the transition via API.
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await db.execute(
                text("UPDATE goals SET status = :s WHERE id = :id"),
                {"s": "awaiting_goal_type", "id": goal_id},
            )
            await db.commit()
        await engine.dispose()

        # Now attempt the transition via the update endpoint
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"


async def test_awaiting_goal_type_cannot_transition_to_verified():
    """Goal in awaiting_goal_type must NOT transition directly to verified."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = resp.json()["id"]

        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await db.execute(
                text("UPDATE goals SET status = :s WHERE id = :id"),
                {"s": "awaiting_goal_type", "id": goal_id},
            )
            await db.commit()
        await engine.dispose()

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "verified"},
        )
        assert resp.status_code == 400


# ─── Worker: deadline worker skips awaiting_goal_type goals ──────────


async def test_deadline_worker_skips_awaiting_goal_type_goals():
    """check_deadlines must not charge or fail goals in awaiting_goal_type."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)

        # Create a goal whose deadline is already past
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                **VALID_GOAL,
                "deadline": past_deadline,
            },
        )
        goal_id = resp.json()["id"]

        # Set it to awaiting_goal_type AND give it a direction_id
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await db.execute(
                text(
                    "UPDATE goals SET status = :s, awaiting_direction_id = :did "
                    "WHERE id = :id"
                ),
                {"s": "awaiting_goal_type", "did": "011-pushup-counter", "id": goal_id},
            )
            await db.commit()
        await engine.dispose()

        # Run the deadline worker — it must NOT process this goal
        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            await check_deadlines()
            mock_charge.assert_not_called()

        # Goal must still be awaiting_goal_type
        engine2 = create_async_engine(settings.database_url, echo=False)
        async_session2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with async_session2() as db:
            result = await db.execute(select(Goal).where(Goal.id == goal_id))
            persisted = result.scalar_one()
            assert persisted.status == "awaiting_goal_type"
        await engine2.dispose()


# ─── Notification: goal_type_ready notification type ──────────────────


async def test_notification_enum_includes_goal_type_ready():
    """Notification.type enum must include 'goal_type_ready'."""
    from app.models.notification import Notification

    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="notifenum@test.com",
            display_name="Notif Enum Tester",
            auth_provider="google",
            auth_provider_id="google-notifenum-1",
        )
        session.add(user)
        await session.commit()

        notif = Notification(
            user_id=user.id,
            type="goal_type_ready",
            title="Goal Type Ready",
            body="Your pushup-counter goal type is ready.",
            created_at=datetime.now(timezone.utc),
        )
        session.add(notif)
        await session.commit()
        notif_id = notif.id

    assert notif_id is not None
    await engine.dispose()


# ─── Existing goal statuses remain unchanged ──────────────────────────


async def test_existing_goal_statuses_still_accepted():
    """All goal statuses — existing + awaiting_goal_type — must be persistable.

    This includes awaiting_goal_type which does NOT exist in the enum yet,
    so this test MUST fail until the migration adds it.
    """
    all_statuses = [
        "draft", "active", "pending_review", "verified", "failed",
        "cancelled", "payment_failed", "awaiting_goal_type",
    ]

    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="existing@test.com",
            display_name="Existing Status Tester",
            auth_provider="google",
            auth_provider_id="google-existing-1",
        )
        session.add(user)
        await session.commit()

        for status in all_statuses:
            goal = Goal(
                user_id=user.id,
                title=f"Goal with status {status}",
                goal_type="youtube_video",
                pledge_amount=1000,
                deadline=datetime.now(timezone.utc) + timedelta(days=30),
                status=status,
            )
            session.add(goal)
        await session.commit()

    # Re-read all statuses
    async with async_session() as session:
        result = await session.execute(
            select(Goal).where(Goal.user_id == user.id)
        )
        persisted = list(result.scalars().all())
        persisted_statuses = {g.status for g in persisted}
        for status in all_statuses:
            assert status in persisted_statuses, f"Status '{status}' not persisted"

    assert len(persisted) == len(all_statuses)
    await engine.dispose()