"""Recurring-goal series continuation.

Shared by every place a goal reaches a resolution the series should survive:
the deadline sweep's failure path and `persist_verification_result`'s success
(and direct-failure) paths. Split out from `app.workers.deadline` so
`app.services.verification_result` can call it too without an import cycle
(`app.workers.deadline` already imports from `verification_result`).
"""

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def calculate_next_deadline(current_deadline: datetime, recurrence: str) -> datetime:
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


async def create_next_recurring_instance(db: AsyncSession, goal_id, user_id):
    """Spawn the next instance of a recurring goal's series, if one doesn't
    already exist for the advanced deadline.

    Callable from any goal-resolution path (verified, failed, deadline-swept)
    — the series must continue regardless of which way a given instance
    resolved, not just on failure.
    """
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

    new_deadline = calculate_next_deadline(row.deadline, recurrence)

    # Idempotency: does the next instance already exist? Required because this
    # is called from more than one resolution path (verified, failed, and a
    # goal blocked on an inconclusive verification that the deadline sweep
    # re-selects every tick) — an unguarded INSERT would spawn a duplicate
    # series member each time it's reached.
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

    # The new instance always starts clean, regardless of how the source
    # instance resolved — a recurring goal's outcome must never carry forward
    # into the next one.
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
