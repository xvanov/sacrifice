import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

from celery import Task
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.celery_app import celery_app
from app.services.verification_result import goal_verification_is_blocked
from app.workers.payments import process_charge_for_goal

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


def _calculate_next_deadline(current_deadline: datetime, recurrence: str) -> datetime:
    if recurrence == "daily":
        return current_deadline + timedelta(days=1)
    elif recurrence == "weekly":
        return current_deadline + timedelta(days=7)
    elif recurrence == "monthly":
        month = current_deadline.month + 1
        year = current_deadline.year
        if month > 12:
            month = 1
            year += 1
        try:
            return current_deadline.replace(year=year, month=month)
        except ValueError:
            import calendar

            last_day = calendar.monthrange(year, month)[1]
            return current_deadline.replace(year=year, month=month, day=last_day)
    raise ValueError(f"Unknown recurrence: {recurrence}")


async def _create_next_recurring_instance(db: AsyncSession, goal_id, user_id):
    result = await db.execute(
        text("""
            SELECT title, description, goal_type, pledge_amount, currency,
                   timezone, recurrence, charity_id, deadline
            FROM goals WHERE id = :id
        """),
        {"id": goal_id},
    )
    row = result.one_or_none()
    if not row:
        return None

    recurrence = row.recurrence
    if not recurrence or recurrence == "none":
        return None

    new_deadline = _calculate_next_deadline(row.deadline, recurrence)

    # Idempotency: does the next instance already exist? Required because this is
    # now also called for a goal that is NOT leaving `active` — a goal blocked on
    # an inconclusive verification is re-selected by every sweep (once a minute),
    # so an unguarded INSERT would spawn a duplicate series member per tick,
    # forever. The normal path gets the same protection for free.
    #
    # (title, goal_type, deadline) per user is the identity of a series member:
    # the row is a copy of this goal with the deadline advanced. A user who owns
    # two identical recurring goals with the same deadline gets one continuation
    # instead of two, which is the safe direction to be wrong in.
    existing = await db.execute(
        text("""
            SELECT id FROM goals
            WHERE user_id = :user_id
              AND title = :title
              AND goal_type = :goal_type
              AND deadline = :deadline
            LIMIT 1
        """),
        {
            "user_id": user_id,
            "title": row.title,
            "goal_type": row.goal_type,
            "deadline": new_deadline,
        },
    )
    already = existing.scalar_one_or_none()
    if already is not None:
        logger.info(
            "Next %s instance of goal %s already exists (%s); not creating another",
            recurrence,
            goal_id,
            already,
        )
        return None

    new_id = uuid.uuid4()

    await db.execute(
        text("""
            INSERT INTO goals
                (id, user_id, title, description, goal_type, pledge_amount,
                 currency, deadline, timezone, recurrence, status, charity_id,
                 created_at, updated_at)
            VALUES
                (:id, :user_id, :title, :description, :goal_type, :pledge_amount,
                 :currency, :deadline, :timezone, :recurrence, :status, :charity_id,
                 :created_at, :updated_at)
        """),
        {
            "id": new_id,
            "user_id": user_id,
            "title": row.title,
            "description": row.description,
            "goal_type": row.goal_type,
            "pledge_amount": row.pledge_amount,
            "currency": row.currency,
            "deadline": new_deadline,
            "timezone": row.timezone,
            "recurrence": recurrence,
            "status": "active",
            "charity_id": row.charity_id,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )

    criteria_result = await db.execute(
        text("""
            SELECT criteria_type, criteria_data FROM goal_criteria WHERE goal_id = :gid
        """),
        {"gid": goal_id},
    )
    criteria_row = criteria_result.one_or_none()
    if criteria_row:
        await db.execute(
            text("""
                INSERT INTO goal_criteria (id, goal_id, criteria_type, criteria_data)
                VALUES (:id, :goal_id, :criteria_type, :criteria_data)
            """),
            {
                "id": uuid.uuid4(),
                "goal_id": new_id,
                "criteria_type": criteria_row.criteria_type,
                "criteria_data": json.dumps(criteria_row.criteria_data),
            },
        )

    now = datetime.now(timezone.utc)
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
            "goal_id": new_id,
            "type": "goal_created",
            "title": f"New Recurring Goal Started: {row.title}",
            "body": f"A new recurring goal has been created for the next period ending {new_deadline.strftime('%Y-%m-%d %H:%M UTC')}.",
            "read": False,
            "created_at": now,
        },
    )

    return str(new_id)


async def _process_expired_goal(db, goal_id, user_id, now):
    goal_id_str = str(goal_id)
    user_id_str = str(user_id)

    # Read the goal before the blocked check: a blocked *recurring* goal still
    # needs its recurrence, and this used to be fetched only on the path that
    # fails the goal.
    result = await db.execute(
        text("SELECT title, recurrence FROM goals WHERE id = :id"),
        {"id": goal_id},
    )
    row = result.one_or_none()
    if not row:
        return
    title = row[0]
    recurrence = row[1]
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
        # commitment. `_create_next_recurring_instance` is idempotent, which is
        # what makes this safe to reach on every sweep while the goal stays
        # blocked.
        if recurring:
            created = await _create_next_recurring_instance(db, goal_id, user_id)
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
        text("UPDATE goals SET status = :status WHERE id = :id"),
        {"status": "failed", "id": goal_id},
    )

    if recurring:
        await _create_next_recurring_instance(db, goal_id, user_id)

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

    # COMMIT before charging. process_charge_for_goal opens its OWN session
    # and updates this same goal row — with our transaction still open we
    # hold the row lock, its UPDATE blocks forever, and every subsequent
    # sweep queues behind the lock (self-deadlock observed live 2026-07-17:
    # all deadline processing silently frozen). The verify-path
    # (persist_verification_result) commits before charging for the same
    # reason.
    await db.commit()

    try:
        await process_charge_for_goal(goal_id_str, user_id_str)
    except Exception as e:
        logger.error("Failed to process charge for goal %s: %s", goal_id_str, e)


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
