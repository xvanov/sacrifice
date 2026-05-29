from typing import Literal

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    action: dict | None = None


class ChatSessionCreateResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    status: Literal["active", "goal_created", "awaiting_goal_type"]