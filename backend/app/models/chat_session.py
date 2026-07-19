import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    pass


class ChatSession(UUIDMixin, TimestampMixin, Base):
    """A chat-driven goal-creation session.

    ``messages`` is a JSONB list of ``{role, content, action}`` dicts
    (see ``app.schemas.chat.ChatMessage``).
    ``draft_goal`` is a JSONB partial goal payload.

    D010 adds the generation linkage: ``session_id`` (external string id used
    in chat API paths; NULL for sessions created before D010), ``goal_id`` +
    ``awaiting_direction_id`` (the pending generated-goal linkage), and
    ``last_activity_at``.
    """

    __tablename__ = "chat_sessions"

    session_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    draft_goal: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    status: Mapped[str] = mapped_column(
        Enum("active", "goal_created", "awaiting_goal_type", name="chat_session_status"),
        default="active",
        nullable=False,
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=True
    )
    awaiting_direction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    user = relationship("User", back_populates="chat_sessions")
