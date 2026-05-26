import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ChatSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=True
    )
    direction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    generation_status: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default=None
    )
    # Values for generation_status: queued, in_progress, pr_open, pr_merged, rejected

    user = relationship("User")
    goal = relationship("Goal")