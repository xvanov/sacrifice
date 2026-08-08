"""The midnight charge buffer: a failed goal resolves immediately, but the
pledge itself is not collected until local midnight on the day it failed.

Three things are pinned here:

1. ``midnight_after`` computes the right UTC instant for a goal's own
   ``timezone``, including the DST/date-boundary edge case where a UTC instant
   maps to the *previous* local calendar day.
2. Both places a goal can resolve to ``failed`` (the deadline sweep, and
   ``persist_verification_result``'s immediate-fail path) set ``charge_after``
   instead of charging on the spot.
3. ``process_deferred_charges`` is genuinely forward-only: a goal with
   ``charge_after IS NULL`` — which is every goal that failed before this
   buffer existed — is invisible to it, never swept in.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal
from app.models.user import User
from app.services.charge_scheduling import midnight_after

pytestmark = pytest.mark.asyncio

CHARGE_BOUNDARY = "app.workers.payments.process_charge_for_goal"


# ─── midnight_after: pure function ───


def test_midnight_after_utc():
    d = datetime(2026, 7, 30, 15, 30, tzinfo=timezone.utc)
    assert midnight_after(d, "UTC") == datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)


def test_midnight_after_respects_local_date_boundary():
    # 03:30 UTC is still 23:30 the PREVIOUS day in New York (EDT, UTC-4) — the
    # buffer must key off the local calendar day, not the UTC one.
    d = datetime(2026, 7, 30, 3, 30, tzinfo=timezone.utc)
    result = midnight_after(d, "America/New_York")
    assert result == datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)


def test_midnight_after_falls_back_to_utc_for_unknown_zone():
    d = datetime(2026, 7, 30, 15, 30, tzinfo=timezone.utc)
    assert midnight_after(d, "Not/AZone") == midnight_after(d, "UTC")


def test_midnight_after_falls_back_to_utc_for_missing_zone():
    d = datetime(2026, 7, 30, 15, 30, tzinfo=timezone.utc)
    assert midnight_after(d, None) == midnight_after(d, "UTC")


def test_midnight_after_treats_naive_datetime_as_utc():
    naive = datetime(2026, 7, 30, 15, 30)
    aware = datetime(2026, 7, 30, 15, 30, tzinfo=timezone.utc)
    assert midnight_after(naive, "UTC") == midnight_after(aware, "UTC")


# ─── integration: the deadline sweep defers instead of charging ───


def _session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _make_expired_goal(db, *, tz="UTC", deadline=None, recurrence="none"):
    user = User(
        email=f"buffer-{uuid.uuid4()}@example.com",
        display_name="Buffer Test",
        auth_provider="google",
        auth_provider_id=str(uuid.uuid4()),
        stripe_customer_id="cus_test_dummy",
    )
    db.add(user)
    await db.flush()

    goal = Goal(
        user_id=user.id,
        title="Buffer Test Goal",
        goal_type="youtube_video",
        pledge_amount=5000,
        currency="usd",
        deadline=deadline or (datetime.now(timezone.utc) - timedelta(minutes=10)),
        timezone=tz,
        recurrence=recurrence,
        status="active",
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal


async def test_deadline_sweep_sets_charge_after_and_does_not_charge_yet():
    from app.workers.deadline import check_deadlines

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            deadline = datetime.now(timezone.utc) - timedelta(minutes=10)
            goal = await _make_expired_goal(db, tz="UTC", deadline=deadline)

        with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
            await check_deadlines()
        charge.assert_not_awaited()

        async with factory() as db:
            result = await db.execute(
                text("SELECT status, charge_after FROM goals WHERE id = :id"),
                {"id": goal.id},
            )
            row = result.one()
            assert row.status == "failed"
            assert row.charge_after is not None
            assert row.charge_after == midnight_after(deadline, "UTC")
    finally:
        await engine.dispose()


async def test_process_deferred_charges_collects_once_buffer_has_passed():
    from app.workers.payments import process_deferred_charges

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _make_expired_goal(db)
            await db.execute(
                text(
                    "UPDATE goals SET status = 'failed', "
                    "charge_after = :ca WHERE id = :id"
                ),
                {"ca": datetime.now(timezone.utc) - timedelta(minutes=1), "id": goal.id},
            )
            await db.commit()

        with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
            result = await process_deferred_charges()

        charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))
        assert result["processed"] == 1

        async with factory() as db:
            charge_after = (
                await db.execute(
                    text("SELECT charge_after FROM goals WHERE id = :id"),
                    {"id": goal.id},
                )
            ).scalar_one()
            # Cleared after the attempt so this row isn't re-swept forever.
            assert charge_after is None
    finally:
        await engine.dispose()


async def test_process_deferred_charges_skips_goals_whose_buffer_has_not_passed():
    from app.workers.payments import process_deferred_charges

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _make_expired_goal(db)
            await db.execute(
                text(
                    "UPDATE goals SET status = 'failed', "
                    "charge_after = :ca WHERE id = :id"
                ),
                {"ca": datetime.now(timezone.utc) + timedelta(hours=6), "id": goal.id},
            )
            await db.commit()

        with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
            result = await process_deferred_charges()

        charge.assert_not_awaited()
        assert result["processed"] == 0
    finally:
        await engine.dispose()


async def test_process_deferred_charges_never_sweeps_a_null_charge_after():
    """Forward-only guarantee: a goal that failed before this buffer existed
    (charge_after left NULL, exactly what the migration does — no backfill)
    must never be picked up, no matter how long it has sat as `failed`."""
    from app.workers.payments import process_deferred_charges

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _make_expired_goal(
                db, deadline=datetime.now(timezone.utc) - timedelta(days=30)
            )
            await db.execute(
                text("UPDATE goals SET status = 'failed' WHERE id = :id"),
                {"id": goal.id},
            )
            await db.commit()
            await db.refresh(goal)
            assert goal.charge_after is None

        with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
            result = await process_deferred_charges()

        charge.assert_not_awaited()
        assert result["processed"] == 0
    finally:
        await engine.dispose()


async def test_immediate_fail_path_defers_charge_too():
    """persist_verification_result's non-active (e.g. pending_review)
    immediate-fail branch sets charge_after rather than charging on the spot."""
    from app.services import verification_result as vr
    from app.models.proof import ProofSubmission

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            deadline = datetime.now(timezone.utc) + timedelta(days=3)
            goal = await _make_expired_goal(db, tz="America/New_York", deadline=deadline)
            await db.execute(
                text("UPDATE goals SET status = 'pending_review' WHERE id = :id"),
                {"id": goal.id},
            )
            # The raw UPDATE above bypasses the ORM — without this, `goal`
            # keeps serving its stale in-memory `status='active'` from the
            # identity map for the rest of this session.
            await db.refresh(goal)
            submission = ProofSubmission(
                goal_id=goal.id,
                submitted_at=datetime.now(timezone.utc),
                proof_data={},
                verification_status="pending",
            )
            db.add(submission)
            await db.commit()
            await db.refresh(submission)

            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db, goal.id, submission.id, vr.FAILED, {"failure_reason": "missed it"}
                )
            charge.assert_not_awaited()

            await db.refresh(goal)
            assert goal.status == "failed"
            assert goal.charge_after == midnight_after(deadline, "America/New_York")
    finally:
        await engine.dispose()
