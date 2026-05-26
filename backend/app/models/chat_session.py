import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ChatSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    draft_goal: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    status: Mapped[str] = mapped_column(
        Enum("active", "goal_created", "awaiting_goal_type", name="chat_session_status"),
        default="active",
        nullable=False,
    )

    user = relationship("User", back_populates="chat_sessions")