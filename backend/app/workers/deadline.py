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
from app.workers.payments import process_charge_for_goal

logger = logging.getLogger(__name__)

GRACE_PERIOD_MINUTES = 5


def _get_session():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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

    result = await db.execute(
        text("SELECT title, recurrence FROM goals WHERE id = :id"),
        {"id": goal_id},
    )
    row = result.one_or_none()
    if not row:
        return
    title = row[0]
    recurrence = row[1]

    await db.execute(
        text("UPDATE goals SET status = :status WHERE id = :id"),
        {"status": "failed", "id": goal_id},
    )

    if recurrence and recurrence != "none":
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
            active_expired = await db.execute(
                text("""
                    SELECT id, user_id FROM goals
                    WHERE status = 'active' AND deadline < :now
                """),
                {"now": now},
            )
            active_rows = list(active_expired)

            for row in active_rows:
                await _process_expired_goal(db, row[0], row[1], now)

            pending_expired = await db.execute(
                text("""
                    SELECT g.id, g.user_id FROM goals g
                    WHERE g.status = 'pending_review' AND g.deadline < :grace_threshold
                """),
                {"grace_threshold": grace_threshold},
            )
            pending_rows = list(pending_expired)

            for row in pending_rows:
                await _process_expired_goal(db, row[0], row[1], now)

            await db.commit()

            return {
                "processed_active": len(active_rows),
                "processed_pending": len(pending_rows),
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
