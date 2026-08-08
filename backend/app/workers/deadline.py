import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta

from celery import Task
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.celery_app import celery_app
from app.services.charge_scheduling import midnight_after
from app.services.recurrence import create_next_recurring_instance
from app.services.verification_result import goal_verification_is_blocked

logger = logging.getLogger(__name__)

GRACE_PERIOD_MINUTES = 5

# Only goals in these statuses are subject to deadline enforcement.
# awaiting_goal_type goals are not yet active and must not be charged.
ENFORCEABLE_STATUSES = frozenset({"active", "pending_review"})


def _get_session():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, session_factory


async def _process_expired_goal(db, goal_id, user_id, now):
    goal_id_str = str(goal_id)
    user_id_str = str(user_id)

    # Read the goal before the blocked check: a blocked *recurring* goal still
    # needs its recurrence, and this used to be fetched only on the path that
    # fails the goal.
    result = await db.execute(
        text("SELECT title, recurrence, deadline, timezone FROM goals WHERE id = :id"),
        {"id": goal_id},
    )
    row = result.one_or_none()
    if not row:
        return
    title = row[0]
    recurrence = row[1]
    goal_deadline = row[2]
    goal_timezone = row[3]
    recurring = bool(recurrence) and recurrence != "none"

    # Never charge a pledge we could not adjudicate. If the goal's latest proof
    # ended `inconclusive` — a GitHub outage, an exhausted rate-limit quota, a
    # sandbox infrastructure fault, criteria we cannot evaluate — the user may
    # well have done the work and we simply failed to check. Both statuses this
    # sweep enforces (`active` and `pending_review`) reach this function, so
    # there is no status a blocked goal could be parked in to escape it; the
    # guard has to live here. The submission is left untouched so the
    # reconciler can retry it, and `needs_operator_review` (set by
    # persist_verification_result once the attempt cap is spent) is the signal
    # for a human to resolve it. Deliberate trade: an unresolved goal is never
    # auto-collected, which we prefer over billing a card for our own outage.
    if await goal_verification_is_blocked(db, goal_id):
        logger.warning(
            "Skipping deadline enforcement for goal %s: verification is "
            "blocked on an inconclusive result (our fault, not the user's). "
            "No status change, no charge.",
            goal_id_str,
        )
        # But the series must not die with it. This goal is parked until an
        # operator resolves it (`sacrifice blocked-goals list`), and returning
        # here without continuing the recurrence silently ended the whole
        # series: the user set up a daily goal, one instance hit a GitHub
        # outage, and every future instance stopped being created — a
        # verification fault of ours quietly cancelling a subscription-like
        # commitment. `create_next_recurring_instance` is idempotent, which is
        # what makes this safe to reach on every sweep while the goal stays
        # blocked.
        if recurring:
            created = await create_next_recurring_instance(db, goal_id, user_id)
            if created:
                await db.commit()
                logger.warning(
                    "Continued %s series for blocked goal %s with new instance %s",
                    recurrence,
                    goal_id_str,
                    created,
                )
        return

    await db.execute(
        text(
            "UPDATE goals SET status = :status, charge_after = :charge_after "
            "WHERE id = :id"
        ),
        {
            "status": "failed",
            "charge_after": midnight_after(goal_deadline, goal_timezone),
            "id": goal_id,
        },
    )

    if recurring:
        await create_next_recurring_instance(db, goal_id, user_id)

    now_val = datetime.now(timezone.utc)
    await db.execute(
        text("""
            INSERT INTO notifications
                (id, user_id, goal_id, type, title, body, read, created_at)
            VALUES
                (:id, :user_id, :goal_id, :type, :title, :body, :read, :created_at)
        """),
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "goal_id": goal_id,
            "type": "goal_failed",
            "title": "Goal Failed",
            "body": f"Your goal '{title}' has failed because the deadline passed without verified proof.",
            "read": False,
            "created_at": now_val,
        },
    )

    # The charge itself is deferred, not dispatched here — see
    # app/services/charge_scheduling.py. The goal is failed now; the pledge is
    # collected at the next local midnight, by process_deferred_charges
    # (app/workers/payments.py). Still committing right away, for the same
    # reason charging used to happen immediately after commit: a long-running
    # transaction holding this row's lock across the rest of this sweep's
    # iterations self-deadlocked live on 2026-07-17.
    await db.commit()


async def check_deadlines():
    now = datetime.now(timezone.utc)
    grace_threshold = now - timedelta(minutes=GRACE_PERIOD_MINUTES)

    engine, session_factory = _get_session()
    async with session_factory() as db:
        try:
            # Enforce only goals in ENFORCEABLE_STATUSES (active and
            # pending_review).  awaiting_goal_type goals are not yet active
            # and must not be charged.
            active_expired = await db.execute(
                text("""
                    SELECT id, user_id FROM goals
                    WHERE status = 'active'
                      AND deadline < :now
                """),
                {"now": now},
            )
            active_rows = list(active_expired)

            for row in active_rows:
                await _process_expired_goal(db, row[0], row[1], now)

            pending_expired = await db.execute(
                text("""
                    SELECT g.id, g.user_id FROM goals g
                    WHERE g.status = 'pending_review'
                      AND g.deadline < :grace_threshold
                """),
                {"grace_threshold": grace_threshold},
            )
            pending_rows = list(pending_expired)

            for row in pending_rows:
                await _process_expired_goal(db, row[0], row[1], now)

            # awaiting_goal_type goals are not yet active — skip charging
            awaiting_past_deadline = await db.execute(
                text("""
                    SELECT COUNT(*) FROM goals
                    WHERE status = 'awaiting_goal_type' AND deadline < :now
                """),
                {"now": now},
            )
            skipped_awaiting = awaiting_past_deadline.scalar()

            await db.commit()

            return {
                "processed_active": len(active_rows),
                "processed_pending": len(pending_rows),
                "skipped_awaiting_goal_type": skipped_awaiting,
            }

        finally:
            await db.close()
            await engine.dispose()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def check_deadlines_task(self: Task):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(check_deadlines())
        return result
    finally:
        loop.close()
