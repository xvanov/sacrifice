import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat_session import ChatSession
from app.models.user import User

router = APIRouter(prefix="/api/chat", tags=["chat"])

GREETING = "Tell me what you want to do, and I'll figure out how to track it."


class ChatMessage(BaseModel):
    role: str
    content: str
    action: dict | None = None


class CreateChatSessionResponse(BaseModel):
    session_id: uuid.UUID
    messages: list[ChatMessage]
    status: str


class RequestNewGoalTypeBody(BaseModel):
    prompt_summary: str


async def _get_owned_session(
    session_id: str, current_user: User, db: AsyncSession
) -> ChatSession:
    """Fetch a chat session by id, verifying it exists and is owned by the user.

    Returns 404 for nonexistent sessions, 403 for sessions owned by others.
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Session not owned by user")
    return session


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateChatSessionResponse,
)
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
        "session_id": session.id,
        "messages": session.messages,
        "status": session.status,
    }


@router.post("/sessions/{session_id}/request-new-goal-type")
async def request_new_goal_type(
    session_id: str,
    body: RequestNewGoalTypeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_session(session_id, current_user, db)
    raise HTTPException(
        status_code=501,
        detail="Goal-type generation is delivered in D010",
    )
