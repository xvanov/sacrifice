from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    goal_id: str | None = None
    type: str
    title: str
    body: str | None = None
    read: bool
    created_at: datetime


class UnreadCountResponse(BaseModel):
    unread_count: int
