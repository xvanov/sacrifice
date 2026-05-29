from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat_session import ChatSession
from app.models.user import User

router = APIRouter(prefix="/api/chat", tags=["chat"])

GREETING = "Tell me what you want to do, and I'll figure out how to track it."


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = ChatSession(
        user_id=current_user.id,
        messages=[{"role": "assistant", "content": GREETING, "action": None}],
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "session_id": str(session.id),
        "messages": session.messages,
        "status": session.status,
    }


@router.post("/sessions/{session_id}/request-new-goal-type", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def request_new_goal_type(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if str(session.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Goal-type generation is delivered in D010",
    )
