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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_goal(client, token: str, *, title: str, deadline: str,
                       pledge_amount: int = 5000, goal_type: str = "youtube_video",
                       criteria: dict | None = None, charity_id: str = "acct_charity_123"):
    if criteria is None:
        criteria = {"min_duration_seconds": 300, "video_description": "test"}
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": title,
            "description": "Test goal for deadline worker",
            "deadline": deadline,
            "pledge_amount": pledge_amount,
            "goal_type": goal_type,
            "criteria": criteria,
            "charity_id": charity_id,
        },
    )
    return resp.json()["id"]


async def _set_goal_status(client, token: str, goal_id: str, status: str):
    """Transition a goal via the real PUT endpoint (application-layer path)."""
    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": status},
    )


def _db_engine_and_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


async def _query_goal(goal_id: str):
    engine, session_factory = _db_engine_and_factory()
    async with session_factory() as db:
        result = await db.execute(select(Goal).where(Goal.id == goal_id))
        goal = result.scalar_one_or_none()
    await engine.dispose()
    return goal


async def _query_payments_for_goal(goal_id: str):
    engine, session_factory = _db_engine_and_factory()
    async with session_factory() as db:
        result = await db.execute(select(Payment).where(Payment.goal_id == goal_id))
        payments = list(result.scalars().all())
    await engine.dispose()
    return payments


async def _query_notifications_for_goal(goal_id: str):
    engine, session_factory = _db_engine_and_factory()
    async with session_factory() as db:
        result = await db.execute(
            select(Notification).where(Notification.goal_id == goal_id)
        )
        notifications = list(result.scalars().all())
    await engine.dispose()
    return notifications


# ---------------------------------------------------------------------------
# Charge side-effect mock
# ---------------------------------------------------------------------------


async def _process_charge_mock_side_effect(goal_id_str: str, user_id_str: str) -> dict:
    """Simulate a successful charge: insert payment + donation_receipt notification."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    engine, session_factory = _db_engine_and_factory()
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
async def test_awaiting_goal_type_goal_skipped_by_deadline_worker():
    """A goal transitioned to awaiting_goal_type via the real PUT endpoint
    must not be enforced when its deadline passes: status stays unchanged,
    no payment is created, and no goal_failed notification is inserted."""
    from app.workers.deadline import check_deadlines

    async with _make_client() as client:
        token, user = await _auth(client)
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        goal_id = await _create_goal(
            client, token,
            title="Pushup Counter Goal",
            deadline=past_deadline,
            pledge_amount=1000,
        )
        # Transition via the real application-layer endpoint (draft → awaiting_goal_type)
        await _set_goal_status(client, token, goal_id, "awaiting_goal_type")

        # Verify the application-level transition persisted correctly
        goal = await _query_goal(goal_id)
        assert goal.status == "awaiting_goal_type"

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
    """An active overdue goal is enforced: status → failed, payment created,
    and goal_failed notification inserted (control case for D010)."""
    from app.workers.deadline import check_deadlines

    async with _make_client() as client:
        token, user = await _auth(client)
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        goal_id = await _create_goal(
            client, token,
            title="Active Goal To Charge",
            deadline=past_deadline,
            pledge_amount=5000,
        )
        await _set_goal_status(client, token, goal_id, "active")

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
async def test_activation_boundary_awaiting_goal_type_skipped_then_active_enforced():
    """An awaiting_goal_type goal is skipped by the deadline worker; after
    transitioning to active via the real accept path (PUT endpoint), the
    same goal is enforced on the next check_deadlines call."""
    from app.workers.deadline import check_deadlines

    async with _make_client() as client:
        token, user = await _auth(client)
        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        goal_id = await _create_goal(
            client, token,
            title="Boundary Test Goal",
            deadline=past_deadline,
            pledge_amount=2000,
        )
        await _set_goal_status(client, token, goal_id, "awaiting_goal_type")

        # --- Phase 1: awaiting_goal_type → skipped ---
        with patch("app.workers.deadline.process_charge_for_goal",
                   side_effect=_process_charge_mock_side_effect):
            await check_deadlines()

        goal = await _query_goal(goal_id)
        assert goal.status == "awaiting_goal_type", (
            f"Phase 1: expected awaiting_goal_type, got {goal.status}"
        )
        payments = await _query_payments_for_goal(goal_id)
        assert len(payments) == 0, "Phase 1: expected 0 payments"

        # --- Phase 2: accept → active, then enforced ---
        await _set_goal_status(client, token, goal_id, "active")

        goal = await _query_goal(goal_id)
        assert goal.status == "active", (
            f"Phase 2 pre-check: expected active, got {goal.status}"
        )

        with patch("app.workers.deadline.process_charge_for_goal",
                   side_effect=_process_charge_mock_side_effect):
            await check_deadlines()

        goal = await _query_goal(goal_id)
        assert goal.status == "failed", (
            f"Phase 2: expected failed, got {goal.status}"
        )

        payments = await _query_payments_for_goal(goal_id)
        assert len(payments) == 1, (
            f"Phase 2: expected 1 payment, got {len(payments)}"
        )
        assert payments[0].status == "succeeded"

        notifications = await _query_notifications_for_goal(goal_id)
        goal_failed = [n for n in notifications if n.type == "goal_failed"]
        assert len(goal_failed) == 1, (
            f"Phase 2: expected 1 goal_failed notification, got {len(goal_failed)}"
        )

