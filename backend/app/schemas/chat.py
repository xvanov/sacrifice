import uuid
from typing import Literal

from pydantic import BaseModel


class ChatMessage(BaseModel):
    """A single chat message with role, content, and optional structured action."""
    role: Literal["user", "assistant"]
    content: str
    action: dict | None = None


class CreateSessionResponse(BaseModel):
    session_id: uuid.UUID
    messages: list[ChatMessage]
    status: Literal["active", "goal_created", "awaiting_goal_type"]