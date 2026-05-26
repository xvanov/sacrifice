import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat_session import ChatSession
from app.models.goal import Goal
from app.models.user import User
from app.schemas.goal import GoalUpdate

router = APIRouter(prefix="/api/chat/sessions", tags=["chat"])


@router.post("/{session_id}/accept-generated-type")
async def accept_generated_type(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Resolve the chat session
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_uuid)
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if str(chat_session.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Require merged state before activation
    if chat_session.generation_status != "pr_merged":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generation is not yet merged",
        )

    # Resolve pending goal
    if chat_session.goal_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending goal found for this session",
        )

    goal_result = await db.execute(
        select(Goal).where(Goal.id == chat_session.goal_id)
    )
    goal = goal_result.scalar_one_or_none()

    if goal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending goal not found for this session",
        )

    if goal.status != "awaiting_goal_type":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Goal is not awaiting goal type activation",
        )

    # Transition goal from awaiting_goal_type to active
    from app.services.goal import update_goal

    await update_goal(db, goal, GoalUpdate(status="active"))

    await db.refresh(goal)

    return {"goal_id": str(goal.id), "status": "active"}