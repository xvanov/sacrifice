import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal
from app.models.notification import Notification
from app.models.payment import Payment


# ---------------------------------------------------------------------------
# Shared helpers — direct-DB, no HTTP routing
# ---------------------------------------------------------------------------


def _db_engine_and_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, session_factory


async def _insert_goal(
    *,
    status: str,
    deadline_offset: timedelta = timedelta(days=-1),
    pledge_amount: int = 5000,
    title: str = "Deadline Worker Test Goal",
    goal_type: str = "youtube_video",
    recurrence: str | None = "none",
) -> str:
    """Insert a goal row directly — no HTTP auth or create endpoint."""
    engine, session_factory = _db_engine_and_factory()
    goal_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    deadline = now + deadline_offset

    async with session_factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO users (id, email, display_name, auth_provider,
                                   auth_provider_id, auth_session_id,
                                   created_at, updated_at)
                VALUES (:id, :email, :display_name, :auth_provider,
                        :auth_provider_id, :auth_session_id,
                        :created_at, :updated_at)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": user_id,
                "email": f"{goal_id.hex[:8]}@test.example",
                "display_name": "Deadline Test User",
                "auth_provider": "google",
                "auth_provider_id": f"auth-{goal_id.hex[:8]}",
                "auth_session_id": str(uuid.uuid4()),
                "created_at": now,
                "updated_at": now,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO goals
                    (id, user_id, title, description, goal_type, pledge_amount,
                     currency, deadline, timezone, recurrence, status, charity_id,
                     created_at, updated_at)
                VALUES
                    (:id, :user_id, :title, :description, :goal_type, :pledge_amount,
                     :currency, :deadline, :timezone, :recurrence, :status, :charity_id,
                     :created_at, :updated_at)
                """
            ),
            {
                "id": goal_id,
                "user_id": user_id,
                "title": title,
                "description": "Direct-insert goal for deadline worker test.",
                "goal_type": goal_type,
                "pledge_amount": pledge_amount,
                "currency": "usd",
                "deadline": deadline,
                "timezone": "UTC",
                "recurrence": recurrence,
                "status": status,
                "charity_id": "acct_charity_d010",
                "created_at": now,
                "updated_at": now,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO goal_criteria (id, goal_id, criteria_type, criteria_data)
                VALUES (:id, :goal_id, :criteria_type, :criteria_data)
                """
            ),
            {
                "id": uuid.uuid4(),
                "goal_id": goal_id,
                "criteria_type": "youtube",
                "criteria_data": '{"min_duration_seconds": 300}',
            },
        )
        await db.commit()

    await engine.dispose()
    return str(goal_id)


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
# Charge side-effect mock (simulates a successful Stripe charge)
# ---------------------------------------------------------------------------


async def _process_charge_mock_side_effect(goal_id_str: str, user_id_str: str) -> dict:
    """Simulate a successful charge: insert payment + donation_receipt notification."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    engine, session_factory = _db_engine_and_factory()
    async with session_factory() as db:
        now = _dt.now(_tz.utc)
        await db.execute(
            text(
                """
                INSERT INTO payments
                    (id, goal_id, user_id, amount, currency,
                     stripe_payment_intent_id, stripe_transfer_id, status, created_at)
                VALUES
                    (:id, :goal_id, :user_id, :amount, :currency,
                     :pi_id, :transfer_id, :status, :created_at)
                """
            ),
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
            text(
                """
                INSERT INTO notifications
                    (id, user_id, goal_id, type, title, body, read, created_at)
                VALUES
                    (:id, :user_id, :goal_id, :type, :title, :body, :read, :created_at)
                """
            ),
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
    return {
        "status": "succeeded",
        "payment_intent_id": "pi_mock_d010",
        "transfer_id": "tr_mock_d010",
        "amount": 5000,
    }


# ---------------------------------------------------------------------------
# Story D010 — deadline worker skips awaiting_goal_type goals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awaiting_goal_type_goal_skipped_by_deadline_worker():
    """awaiting_goal_type goals must not be enforced even past deadline:
    status unchanged, no payment, no goal_failed notification."""
    from app.workers.deadline import check_deadlines

    goal_id = await _insert_goal(status="awaiting_goal_type")

    goal = await _query_goal(goal_id)
    assert goal.status == "awaiting_goal_type"

    with patch(
        "app.workers.deadline.process_charge_for_goal",
        side_effect=_process_charge_mock_side_effect,
    ):
        await check_deadlines()

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
    """Active overdue goal is enforced: status → failed, payment created,
    goal_failed notification inserted (control case for D010)."""
    from app.workers.deadline import check_deadlines

    goal_id = await _insert_goal(status="active")

    goal = await _query_goal(goal_id)
    assert goal.status == "active"

    with patch(
        "app.workers.deadline.process_charge_for_goal",
        side_effect=_process_charge_mock_side_effect,
    ):
        await check_deadlines()

    goal = await _query_goal(goal_id)
    assert goal.status == "failed", f"Expected failed, got {goal.status}"

    payments = await _query_payments_for_goal(goal_id)
    assert len(payments) == 1, f"Expected 1 payment, got {len(payments)}"
    assert payments[0].status == "succeeded"

    notifications = await _query_notifications_for_goal(goal_id)
    goal_failed = [n for n in notifications if n.type == "goal_failed"]
    assert len(goal_failed) == 1, (
        f"Expected 1 goal_failed notification, got {len(goal_failed)}"
    )


@pytest.mark.asyncio
async def test_pending_review_past_grace_threshold_enforced():
    """A pending_review goal whose deadline is past the grace threshold
    IS enforced — confirms no change to existing enforceable-state handling."""
    from app.workers.deadline import check_deadlines
    from app.workers.deadline import GRACE_PERIOD_MINUTES

    # Deadline far enough back to be past the grace threshold.
    offset = timedelta(minutes=-(GRACE_PERIOD_MINUTES + 5))
    goal_id = await _insert_goal(status="pending_review", deadline_offset=offset)

    goal = await _query_goal(goal_id)
    assert goal.status == "pending_review"

    with patch(
        "app.workers.deadline.process_charge_for_goal",
        side_effect=_process_charge_mock_side_effect,
    ):
        await check_deadlines()

    goal = await _query_goal(goal_id)
    assert goal.status == "failed", f"Expected failed, got {goal.status}"

    payments = await _query_payments_for_goal(goal_id)
    assert len(payments) == 1, f"Expected 1 payment, got {len(payments)}"
    assert payments[0].status == "succeeded"

    notifications = await _query_notifications_for_goal(goal_id)
    goal_failed = [n for n in notifications if n.type == "goal_failed"]
    assert len(goal_failed) == 1, (
        f"Expected 1 goal_failed notification, got {len(goal_failed)}"
    )



def test_beat_schedule_references_registered_tasks():
    """Every beat entry must name a task Celery actually registered.

    Regression: the beat schedule pointed at ``...deadline.check_deadlines``
    (the bare coroutine, never registered), so beat emitted "unregistered
    task" every 60s and deadlines were never enforced. Guard all beat entries,
    not just this one.
    """
    from app.core.celery_app import celery_app

    registered = set(celery_app.tasks.keys())
    for name, entry in celery_app.conf.beat_schedule.items():
        assert entry["task"] in registered, (
            f"beat entry {name!r} references unregistered task {entry['task']!r}; "
            f"registered tasks include: "
            f"{sorted(t for t in registered if 'deadline' in t or 'payment' in t)}"
        )


@pytest.mark.asyncio
async def test_deadline_charge_runs_with_real_worker_without_deadlocking():
    """Regression: the sweep must COMMIT its own goal update before invoking
    the real charge worker. process_charge_for_goal opens a second session
    and updates the same goal row — with the sweep's transaction still open
    that UPDATE blocked on the row lock forever, silently freezing all
    deadline processing (observed live 2026-07-17). Run the REAL charge
    function (Stripe mocked) under a timeout: a deadlock fails fast here
    instead of hanging the suite.
    """
    import asyncio
    from unittest.mock import MagicMock

    goal_id = await _insert_goal(status="active")

    pi = MagicMock()
    pi.id = "pi_deadlock_test"
    pi.status = "succeeded"
    with (
        patch("app.workers.payments._resolve_payment_method", return_value="pm_test"),
        patch("app.workers.payments.stripe.PaymentIntent.create", return_value=pi),
        patch("app.workers.payments.stripe.PaymentIntent.retrieve", return_value=pi),
    ):
        from app.workers.deadline import check_deadlines

        result = await asyncio.wait_for(check_deadlines(), timeout=15)

    assert result["processed_active"] >= 1
    goal = await _query_goal(goal_id)
    assert goal.status == "failed"
    payments = await _query_payments_for_goal(goal_id)
    assert len(payments) == 1
    assert payments[0].status == "succeeded"
