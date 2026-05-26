import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class PostMessageRequest(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be empty or whitespace")
        return v


class CreateGoalRequest(BaseModel):
    goal_payload: dict


class RequestNewGoalTypeRequest(BaseModel):
    prompt_summary: str


class ChatMessage(BaseModel):
    role: str
    content: str
    action: dict | None = None


class SessionResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    status: str


class MessagesResponse(BaseModel):
    messages: list[ChatMessage]
    draft_goal: dict | None = None


class CreateGoalResponse(BaseModel):
    goal_id: str
    status: str