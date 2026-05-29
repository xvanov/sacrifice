import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import Goal, GoalCriteria
from app.schemas.goal import GoalCreate, GoalUpdate

TYPE_TO_CRITERIA_TYPE = {
    "youtube_video": "youtube",
    "api_endpoint": "api_endpoint",
    "dev_sandbox": "dev_sandbox",
    "github_repo": "github_repo",
}

ALLOWED_TRANSITIONS = {
    None: {"draft"},
    "draft": {"active", "cancelled", "awaiting_goal_type"},
    "active": {"pending_review", "cancelled", "failed"},
    "pending_review": {"verified", "failed"},
    "awaiting_goal_type": {"active"},
}


async def create_goal(db: AsyncSession, user_id: uuid.UUID, data: GoalCreate) -> Goal:
    goal = Goal(
        user_id=user_id,
        title=data.title,
        description=data.description,
        goal_type=data.goal_type,
        pledge_amount=data.pledge_amount,
        currency=data.currency,
        deadline=data.deadline,
        timezone=data.timezone,
        recurrence=data.recurrence,
        status="draft",
        charity_id=data.charity_id,
    )
    db.add(goal)
    await db.flush()

    criteria_type = TYPE_TO_CRITERIA_TYPE.get(data.goal_type, data.goal_type)
    criteria = GoalCriteria(
        goal_id=goal.id,
        criteria_type=criteria_type,
        criteria_data=data.criteria,
    )
    db.add(criteria)
    await db.commit()
    return goal


async def get_goal_criteria(db: AsyncSession, goal_id: uuid.UUID) -> GoalCriteria | None:
    result = await db.execute(
        select(GoalCriteria).where(GoalCriteria.goal_id == goal_id)
    )
    return result.scalar_one_or_none()


async def get_user_goals(
    db: AsyncSession, user_id: uuid.UUID, status: str | None = None
) -> list[Goal]:
    stmt = select(Goal).where(Goal.user_id == user_id)
    if status:
        stmt = stmt.where(Goal.status == status)
    stmt = stmt.order_by(Goal.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_goal_by_id(db: AsyncSession, goal_id: uuid.UUID) -> Goal | None:
    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    return result.scalar_one_or_none()


async def update_goal(
    db: AsyncSession, goal: Goal, data: GoalUpdate
) -> Goal:
    if data.status is not None and data.status != goal.status:
        allowed = ALLOWED_TRANSITIONS.get(goal.status, set())
        if data.status not in allowed:
            raise ValueError(
                f"Cannot transition from '{goal.status}' to '{data.status}'"
            )

    set_clauses = []
    params = {}

    for field in ("title", "description", "deadline", "pledge_amount", "charity_id", "timezone"):
        value = getattr(data, field, None)
        if value is not None:
            set_clauses.append(f"{field} = :{field}")
            params[field] = value

    if data.status is not None and data.status != goal.status:
        set_clauses.append("status = :new_status")
        params["new_status"] = data.status

    if set_clauses:
        params["goal_id"] = goal.id
        await db.execute(
            text(f"UPDATE goals SET {', '.join(set_clauses)} WHERE id = :goal_id"),
            params,
        )
        await db.commit()

    result = await db.execute(
        select(Goal).where(Goal.id == goal.id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def delete_goal(db: AsyncSession, goal: Goal) -> None:
    criteria = await get_goal_criteria(db, goal.id)
    if criteria:
        await db.execute(
            text("DELETE FROM goal_criteria WHERE id = :id"),
            {"id": criteria.id},
        )
    await db.execute(
        text("DELETE FROM notifications WHERE goal_id = :goal_id"),
        {"goal_id": goal.id},
    )
    await db.execute(
        text("DELETE FROM proof_submissions WHERE goal_id = :goal_id"),
        {"goal_id": goal.id},
    )
    await db.execute(
        text("DELETE FROM payments WHERE goal_id = :goal_id"),
        {"goal_id": goal.id},
    )
    await db.execute(
        text("DELETE FROM goals WHERE id = :id"),
        {"id": goal.id},
    )
    await db.commit()
