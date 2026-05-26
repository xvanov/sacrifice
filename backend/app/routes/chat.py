from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat import ChatSession
from app.models.user import User

router = APIRouter(prefix="/api/chat", tags=["chat"])


class RequestNewGoalTypeBody(BaseModel):
    prompt_summary: str


@router.post("/sessions/{session_id}/request-new-goal-type")
async def request_new_goal_type(
    session_id: str,
    body: RequestNewGoalTypeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Goal-type generation is delivered in D010",
    )