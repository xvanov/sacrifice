from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.payment import Payment
from app.models.user import User
from app.services.notification import get_unread_count

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = current_user.id

    total_result = await db.execute(
        select(func.count(Goal.id)).where(Goal.user_id == user_id)
    )
    total_goals = total_result.scalar() or 0

    completed_result = await db.execute(
        select(func.count(Goal.id)).where(
            Goal.user_id == user_id, Goal.status == "verified"
        )
    )
    completed_count = completed_result.scalar() or 0

    failed_result = await db.execute(
        select(func.count(Goal.id)).where(
            Goal.user_id == user_id, Goal.status == "failed"
        )
    )
    failed_count = failed_result.scalar() or 0

    pledged_result = await db.execute(
        select(func.coalesce(func.sum(Goal.pledge_amount), 0)).where(
            Goal.user_id == user_id
        )
    )
    total_pledged = pledged_result.scalar() or 0

    saved_result = await db.execute(
        select(func.coalesce(func.sum(Goal.pledge_amount), 0)).where(
            Goal.user_id == user_id, Goal.status == "verified"
        )
    )
    total_saved = saved_result.scalar() or 0

    donated_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.user_id == user_id, Payment.status == "succeeded"
        )
    )
    total_donated = donated_result.scalar() or 0

    denominator = completed_count + failed_count
    success_rate = (completed_count / denominator * 100) if denominator > 0 else 0.0

    unread_notifications = await get_unread_count(db, user_id)

    return {
        "total_goals": total_goals,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "success_rate": round(success_rate, 1),
        "total_pledged": total_pledged,
        "total_donated": total_donated,
        "total_saved": total_saved,
        "unread_notifications": unread_notifications,
    }


@router.get("/history")
async def get_dashboard_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Goal)
        .where(Goal.user_id == current_user.id)
        .order_by(Goal.created_at.desc())
    )
    goals = result.scalars().all()

    return [
        {
            "id": str(goal.id),
            "title": goal.title,
            "status": goal.status,
            "goal_type": goal.goal_type,
            "pledge_amount": goal.pledge_amount,
            "deadline": goal.deadline.isoformat(),
            "created_at": goal.created_at.isoformat(),
        }
        for goal in goals
    ]
