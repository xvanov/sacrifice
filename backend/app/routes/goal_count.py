from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.user import User

router = APIRouter(prefix="/api/goals", tags=["goals"])

RECURRENCE_OPTIONS = ["none", "daily", "weekly", "monthly"]


@router.get("/recurrence-options")
async def get_recurrence_options():
    return {"options": RECURRENCE_OPTIONS}


@router.get("/count")
async def get_goal_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(func.count()).select_from(Goal).where(Goal.user_id == current_user.id)
    )
    count = result.scalar_one()
    return {"count": count}
