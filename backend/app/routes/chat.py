from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models import User, ChatSession
from app.schemas.chat import ChatSessionCreateResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])

GREETING_MESSAGE = "Tell me what you want to do, and I'll figure out how to track it."


@router.post("/sessions", status_code=201, response_model=ChatSessionCreateResponse)
async def create_chat_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = ChatSession(
        user_id=current_user.id,
        messages=[{"role": "assistant", "content": GREETING_MESSAGE, "action": None}],
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return ChatSessionCreateResponse(
        session_id=str(session.id),
        messages=[{"role": "assistant", "content": GREETING_MESSAGE, "action": None}],
        status="active",
    )