from typing import TypedDict

from pydantic import BaseModel


class ChatMessage(TypedDict, total=False):
    role: str  # "user" | "assistant"
    content: str
    action: dict | None


class CreateSessionResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    status: str