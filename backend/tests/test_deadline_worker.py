import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models.goal import Goal
from app.models.payment import Payment
from app.models.notification import Notification
from app.config import settings


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


async def _query_goal(goal_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(select(Goal).where(Goal.id == goal_id))
        goal = result.scalar_one_or_none()
    await engine.dispose()
    return goal


async def _query_payments_for_goal(goal_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(select(Payment).where(Payment.goal_id == goal_id))
        payments = list(result.scalars().all())
    await engine.dispose()
    return payments


async def _query_notifications_for_goal(goal_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(
            select(Notification).where(Notification.goal_id == goal_id)
        )
        notifications = list(result.scalars().all())
    await engine.dispose()
    return notifications


async def _force_goal_status(goal_id: str, status: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE goals SET status = :status WHERE id = :id"),
            {"status": status, "id": uuid.UUID(goal_id)},
        )
        await db.commit()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Mock for process_charge_for_goal that simulates persisted side effects
# without opening a second DB session (avoids the pre-existing deadlock
# between check_deadlines' session and process_charge_for_goal's session).
# ---------------------------------------------------------------------------

async def _process_charge_mock_side_effect(goal_id_str: str, user_id_str: str) -> dict:
    """Simulate a successful charge: insert payment + donation_receipt notification.

    Uses its own session/engine, but since check_deadlines has already
    committed its status change by the time this mock runs (the mock is
    called AFTER _process_expired_goal has done its updates but BEFORE
    check_deadlines commits), we must be careful not to conflict.

    In practice this mock is called within the same event-loop turn as
    _process_expired_goal's updates.  But _process_expired_goal only
    updates goal status + inserts a goal_failed notification — neither
    of which conflicts with inserting into payments or notifications.
    The goal row itself is not updated again by this mock.
    """
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        now = _dt.now(_tz.utc)
        await db.execute(
            text("""
                INSERT INTO payments
                    (id, goal_id, user_id, amount, currency,
                     stripe_payment_intent_id, stripe_transfer_id, status, created_at)
                VALUES
                    (:id, :goal_id, :user_id, :amount, :currency,
                     :pi_id, :transfer_id, :status, :created_at)
            """),
            {
                "id": _uuid.uuid4(),
                "goal_id": _uuid.UUID(goal_id_str),
                "user_id": _uuid.UUID(user_id_str),
                "amount": 5000,
                "currency": "usd",
                "pi_id": "pi_mock_d010",
                "transfer_id": "tr_mock_d010",
                "status": "succeeded",
                "created_at": now,
            },
        )
        await db.execute(
            text("""
                INSERT INTO notifications
                    (id, user_id, goal_id, type, title, body, read, created_at)
                VALUES
                    (:id, :user_id, :goal_id, :type, :title, :body, :read, :created_at)
            """),
            {
                "id": _uuid.uuid4(),
                "user_id": _uuid.UUID(user_id_str),
                "goal_id": _uuid.UUID(goal_id_str),
                "type": "donation_receipt",
                "title": "Donation Receipt",
                "body": "Your pledge has been charged and donated.",
                "read": False,
                "created_at": now,
            },
        )
        await db.commit()
    await engine.dispose()
    return {"status": "succeeded", "payment_intent_id": "pi_mock_d010",
            "transfer_id": "tr_mock_d010", "amount": 5000}


# ---------------------------------------------------------------------------
# Story D010: deadline worker skips awaiting_goal_type goals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awaiting_goal_type_goal_not_enforced_by_deadline_worker():
    """An awaiting_goal_type goal past its deadline must not be transitioned
    to failed and must not trigger a charge.

    Asserts on persisted outcome after running the real check_deadlines:
    status unchanged, no payment row, no goal_failed notification."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Pushup Counter Goal",
                "description": "Do 20 pushups every morning at 7am verified with phone camera",
                "deadline": past_deadline,
                "pledge_amount": 1000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 60, "video_description": "pushups"},
                "charity_id": "acct_charity_123",
            },
        )
        goal_id = resp.json()["id"]
        await _force_goal_status(goal_id, "awaiting_goal_type")

        goal = await _query_goal(goal_id)
        assert goal.status == "awaiting_goal_type"

        # process_charge_for_goal must not be reached for awaiting_goal_type
        # goals.  We mock it so that if it IS called the test fails via the
        # persisted side effects it writes (payment + donation_receipt).
        with patch("app.workers.deadline.process_charge_for_goal",
                   side_effect=_process_charge_mock_side_effect):
            await check_deadlines()

        # --- Persisted outcome: no enforcement ---
        goal = await _query_goal(goal_id)
        assert goal.status == "awaiting_goal_type", (
            f"Expected awaiting_goal_type, got {goal.status}"
        )

        payments = await _query_payments_for_goal(goal_id)
        assert len(payments) == 0, (
            f"Expected 0 payments for awaiting_goal_type goal, got {len(payments)}"
        )

        notifications = await _query_notifications_for_goal(goal_id)
        goal_failed_notifs = [n for n in notifications if n.type == "goal_failed"]
        assert len(goal_failed_notifs) == 0, (
            f"Expected 0 goal_failed notifications, got {len(goal_failed_notifs)}"
        )


@pytest.mark.asyncio
async def test_active_overdue_goal_enforced_by_deadline_worker():
    """An active overdue goal is still enforced by the deadline worker:
    status transitions to failed, a goal_failed notification is created,
    and the charge side effects are persisted."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Active Goal To Charge",
                "description": "Should be enforced",
                "deadline": past_deadline,
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "test"},
                "charity_id": "acct_charity_123",
            },
        )
        goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        goal = await _query_goal(goal_id)
        assert goal.status == "active"

        with patch("app.workers.deadline.process_charge_for_goal",
                   side_effect=_process_charge_mock_side_effect):
            await check_deadlines()

        # --- Persisted enforcement outcomes ---
        goal = await _query_goal(goal_id)
        assert goal.status == "failed", (
            f"Expected failed, got {goal.status}"
        )

        payments = await _query_payments_for_goal(goal_id)
        assert len(payments) == 1, (
            f"Expected 1 payment, got {len(payments)}"
        )
        assert payments[0].status == "succeeded"

        notifications = await _query_notifications_for_goal(goal_id)
        goal_failed = [n for n in notifications if n.type == "goal_failed"]
        assert len(goal_failed) == 1, (
            f"Expected 1 goal_failed notification, got {len(goal_failed)}"
        )


@pytest.mark.asyncio
async def test_mixed_set_active_enforced_awaiting_goal_type_skipped():
    """When both an active overdue goal and an awaiting_goal_type overdue goal
    exist, only the active goal is enforced.  The awaiting_goal_type goal
    remains untouched (status unchanged, no payment, no notification)."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        # --- Active overdue goal ---
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Active Goal To Be Charged",
                "description": "Should be enforced",
                "deadline": past_deadline,
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "test"},
                "charity_id": "acct_charity_123",
            },
        )
        active_goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{active_goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        # --- awaiting_goal_type overdue goal ---
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Generated Pushup Verifier",
                "description": "Awaiting goal type generation",
                "deadline": past_deadline,
                "pledge_amount": 1000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 60, "video_description": "pushups"},
                "charity_id": "acct_charity_123",
            },
        )
        gen_goal_id = resp.json()["id"]
        await _force_goal_status(gen_goal_id, "awaiting_goal_type")

        gen_goal = await _query_goal(gen_goal_id)
        assert gen_goal.status == "awaiting_goal_type"

        with patch("app.workers.deadline.process_charge_for_goal",
                   side_effect=_process_charge_mock_side_effect):
            await check_deadlines()

        # --- Active goal: enforced ---
        active_goal = await _query_goal(active_goal_id)
        assert active_goal.status == "failed", (
            f"Active goal should be failed, got {active_goal.status}"
        )

        active_payments = await _query_payments_for_goal(active_goal_id)
        assert len(active_payments) == 1, (
            f"Expected 1 payment for active goal, got {len(active_payments)}"
        )

        active_notifs = await _query_notifications_for_goal(active_goal_id)
        goal_failed = [n for n in active_notifs if n.type == "goal_failed"]
        assert len(goal_failed) == 1, (
            f"Expected 1 goal_failed notification, got {len(goal_failed)}"
        )

        # --- awaiting_goal_type goal: untouched ---
        gen_goal = await _query_goal(gen_goal_id)
        assert gen_goal.status == "awaiting_goal_type", (
            f"Generated goal should stay awaiting_goal_type, got {gen_goal.status}"
        )

        gen_payments = await _query_payments_for_goal(gen_goal_id)
        assert len(gen_payments) == 0, (
            f"Expected 0 payments for awaiting_goal_type goal, got {len(gen_payments)}"
        )

        gen_notifs = await _query_notifications_for_goal(gen_goal_id)
        gen_failed = [n for n in gen_notifs if n.type == "goal_failed"]
        assert len(gen_failed) == 0, (
            f"Expected 0 goal_failed notifications for awaiting_goal_type goal, "
            f"got {len(gen_failed)}"
        )


@pytest.mark.asyncio
async def test_activation_boundary_awaiting_goal_type_skipped_then_active_enforced():
    """Pre-acceptance: awaiting_goal_type goal skipped by deadline worker.
    Post-acceptance (after transition to active): same goal enforced.

    Covers the activation boundary from the story flow — a generated goal
    must not be enforced before the user accepts it (finding #4)."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Pre-activation Goal",
                "description": "Starts as awaiting_goal_type, then gets accepted",
                "deadline": past_deadline,
                "pledge_amount": 2000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 60, "video_description": "test"},
                "charity_id": "acct_charity_123",
            },
        )
        goal_id = resp.json()["id"]
        await _force_goal_status(goal_id, "awaiting_goal_type")

        # --- Pre-acceptance: deadline worker must skip ---
        with patch("app.workers.deadline.process_charge_for_goal",
                   side_effect=_process_charge_mock_side_effect):
            await check_deadlines()

        goal = await _query_goal(goal_id)
        assert goal.status == "awaiting_goal_type", (
            f"Pre-acceptance: expected awaiting_goal_type, got {goal.status}"
        )
        pre_payments = await _query_payments_for_goal(goal_id)
        assert len(pre_payments) == 0, "Pre-acceptance: expected 0 payments"

        pre_notifs = await _query_notifications_for_goal(goal_id)
        pre_failed = [n for n in pre_notifs if n.type == "goal_failed"]
        assert len(pre_failed) == 0, "Pre-acceptance: expected 0 goal_failed notifications"

        # --- Simulate acceptance: transition to active ---
        await _force_goal_status(goal_id, "active")
        goal = await _query_goal(goal_id)
        assert goal.status == "active"

        # --- Post-acceptance: deadline worker must now enforce ---
        with patch("app.workers.deadline.process_charge_for_goal",
                   side_effect=_process_charge_mock_side_effect):
            await check_deadlines()

        goal = await _query_goal(goal_id)
        assert goal.status == "failed", (
            f"Post-acceptance: expected failed, got {goal.status}"
        )

        post_payments = await _query_payments_for_goal(goal_id)
        assert len(post_payments) == 1, (
            f"Post-acceptance: expected 1 payment, got {len(post_payments)}"
        )

        post_notifs = await _query_notifications_for_goal(goal_id)
        post_failed = [n for n in post_notifs if n.type == "goal_failed"]
        assert len(post_failed) == 1, (
            f"Post-acceptance: expected 1 goal_failed notification, "
            f"got {len(post_failed)}"
        )