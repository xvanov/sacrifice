from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat_session import ChatSession
from app.models.user import User
from app.schemas.chat import CreateSessionResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])

GREETING_MESSAGE = {
    "role": "assistant",
    "content": "Tell me what you want to do, and I'll figure out how to track it.",
    "action": None,
}


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateSessionResponse,
)
async def create_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session with an initial assistant greeting."""
    session = ChatSession(
        user_id=current_user.id,
        messages=[GREETING_MESSAGE],
        draft_goal=None,
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