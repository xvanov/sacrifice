from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str
    action: dict | None = None


class ChatSessionCreateResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    status: str