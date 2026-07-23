import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models.goal import Goal, GoalCriteria
from app.models.notification import Notification
from app.config import settings


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None, "email_verified": True}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_active_recurring_goal(client, token, recurrence="daily",
                                         deadline_delta_days=1):
    # Create + activate with a future deadline (the guard rejects a past or
    # within-the-hour deadline), then backdate it in the DB to simulate the
    # expired goal the deadline sweep is meant to roll over.
    future_deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Recurring Test Goal",
            "description": "A recurring goal past deadline",
            "deadline": future_deadline,
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 300, "video_description": "Test content"},
            "charity_id": "acct_charity_connect_123",
            "recurrence": recurrence,
        },
    )
    goal_id = resp.json()["id"]

    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )

    await _backdate_deadline(
        goal_id, datetime.now(timezone.utc) - timedelta(days=deadline_delta_days)
    )
    return goal_id


async def _backdate_deadline(goal_id: str, when: datetime):
    """Push a goal's deadline into the past directly in the DB, bypassing the
    create/activate guard that forbids past-or-within-the-hour deadlines. This
    is the only way to set up the 'expired active goal' the deadline sweep acts
    on, since the API refuses to create one."""
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE goals SET deadline = :d WHERE id = :g"),
            {"d": when, "g": goal_id},
        )
        await db.commit()
    await engine.dispose()


async def _query_all_goals_for_user(user_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(
            select(Goal).where(Goal.user_id == user_id).order_by(Goal.created_at)
        )
        goals = list(result.scalars().all())
    await engine.dispose()
    return goals


async def _query_notifications(user_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(
            select(Notification).where(Notification.user_id == user_id)
            .order_by(Notification.created_at)
        )
        notifications = list(result.scalars().all())
    await engine.dispose()
    return notifications


async def _query_goal_criteria(goal_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(
            select(GoalCriteria).where(GoalCriteria.goal_id == goal_id)
        )
        criteria = result.scalar_one_or_none()
    await engine.dispose()
    return criteria


# --- Acceptance Criterion: Recurring daily goal creates new instance ---

async def test_recurring_daily_goal_creates_new_instance():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_recurring_goal(client, token, recurrence="daily")

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        goals = await _query_all_goals_for_user(user["id"])
        assert len(goals) == 2, f"Expected 2 goals, got {len(goals)}"

        old_goal = next(g for g in goals if str(g.id) == goal_id)
        new_goal = next(g for g in goals if str(g.id) != goal_id)

        assert old_goal.status == "failed"
        assert new_goal.status == "active"
        assert new_goal.title == old_goal.title
        assert new_goal.description == old_goal.description
        assert new_goal.goal_type == old_goal.goal_type
        assert new_goal.pledge_amount == old_goal.pledge_amount
        assert new_goal.recurrence == "daily"

        expected_deadline = old_goal.deadline + timedelta(days=1)
        assert abs((new_goal.deadline - expected_deadline).total_seconds()) < 60, \
            f"New deadline {new_goal.deadline} not within 60s of expected {expected_deadline}"


async def test_recurring_daily_goal_new_instance_has_copied_criteria():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_recurring_goal(client, token, recurrence="daily")

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        goals = await _query_all_goals_for_user(user["id"])
        new_goal = next(g for g in goals if str(g.id) != goal_id)

        new_criteria = await _query_goal_criteria(str(new_goal.id))
        assert new_criteria is not None
        assert new_criteria.criteria_data["min_duration_seconds"] == 300
        assert new_criteria.criteria_data["video_description"] == "Test content"


# --- Acceptance Criterion: Recurring weekly goals reset on same day of week ---

async def test_recurring_weekly_goal_creates_new_instance():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_recurring_goal(client, token, recurrence="weekly",
                                                       deadline_delta_days=7)

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        goals = await _query_all_goals_for_user(user["id"])
        assert len(goals) == 2

        old_goal = next(g for g in goals if str(g.id) == goal_id)
        new_goal = next(g for g in goals if str(g.id) != goal_id)

        assert new_goal.recurrence == "weekly"
        expected_deadline = old_goal.deadline + timedelta(days=7)
        assert abs((new_goal.deadline - expected_deadline).total_seconds()) < 60


# --- Acceptance Criterion: Recurring monthly goals reset on same day of month ---

async def test_recurring_monthly_goal_creates_new_instance():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)

        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Monthly Recurring Goal",
                "description": "Monthly goal past deadline",
                "deadline": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "Monthly test"},
                "charity_id": "acct_charity_connect_123",
                "recurrence": "monthly",
            },
        )
        goal_id = resp.json()["id"]

        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        await _backdate_deadline(goal_id, datetime.now(timezone.utc) - timedelta(days=30))

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        goals = await _query_all_goals_for_user(user["id"])
        assert len(goals) == 2

        old_goal = next(g for g in goals if str(g.id) == goal_id)
        new_goal = next(g for g in goals if str(g.id) != goal_id)

        assert new_goal.recurrence == "monthly"
        expected_month = old_goal.deadline.month % 12 + 1
        assert new_goal.deadline.month == expected_month or \
               (old_goal.deadline.month == 12 and new_goal.deadline.month == 1)


# --- Acceptance Criterion: Non-recurring goal does NOT create new instance ---

async def test_non_recurring_goal_does_not_create_new_instance():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)

        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Non-Recurring Goal",
                "description": "Standard goal",
                "deadline": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "Test"},
                "charity_id": "acct_charity_connect_123",
                "recurrence": "none",
            },
        )
        goal_id = resp.json()["id"]

        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        await _backdate_deadline(goal_id, datetime.now(timezone.utc) - timedelta(days=1))

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        goals = await _query_all_goals_for_user(user["id"])
        assert len(goals) == 1, f"Expected 1 goal, got {len(goals)}"


# --- Acceptance Criterion: Notification created for new recurring instance ---

async def test_recurring_goal_creates_notification_for_new_instance():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_recurring_goal(client, token, recurrence="daily")

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        notifications = await _query_notifications(user["id"])
        instance_notifications = [
            n for n in notifications
            if n.type == "goal_created" and "Recurring" in n.title
        ]
        assert len(instance_notifications) >= 1, \
            f"Expected at least 1 'goal_created' notification for new instance, found {len(instance_notifications)}"


# --- Acceptance Criterion: Notification created for failure ---

async def test_recurring_goal_creates_notification_for_failure():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_recurring_goal(client, token, recurrence="daily")

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        notifications = await _query_notifications(user["id"])
        goal_failed = [
            n for n in notifications if n.type == "goal_failed"
        ]
        assert len(goal_failed) >= 1, \
            f"Expected at least 1 'goal_failed' notification, found {len(goal_failed)}"


# --- Acceptance Criterion: Recurring goal past deadline with pending_review creates new instance ---

async def test_recurring_goal_pending_review_past_grace_creates_new_instance():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)

        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Recurring Grace Goal",
                "description": "In grace period",
                "deadline": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "Test"},
                "charity_id": "acct_charity_connect_123",
                "recurrence": "daily",
            },
        )
        goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "pending_review"},
        )
        # Backdate AFTER reaching pending_review (each transition is guarded).
        await _backdate_deadline(goal_id, datetime.now(timezone.utc) - timedelta(minutes=10))

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        goals = await _query_all_goals_for_user(user["id"])
        assert len(goals) == 2
        new_goal = next(g for g in goals if str(g.id) != goal_id)
        assert new_goal.status == "active"
        assert new_goal.recurrence == "daily"
