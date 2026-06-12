import uuid
from typing import Any, Literal

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


class CreateGoalRequest(BaseModel):
    """Request body for POST /api/chat/sessions/{session_id}/create-goal."""
    goal_payload: dict[str, Any]


class CreateGoalResponse(BaseModel):
    """Response body for POST /api/chat/sessions/{session_id}/create-goal."""
    goal_id: str
    status: str