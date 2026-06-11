import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.schemas.chat import ChatMessage


class ChatSession(UUIDMixin, TimestampMixin, Base):
    """A chat-driven goal-creation session.

    ``messages`` is a JSONB list of ``{role, content, action}`` dicts
    (see ``app.schemas.chat.ChatMessage``).
    ``draft_goal`` is a JSONB partial goal payload.
    """

    __tablename__ = "chat_sessions"

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

    user = relationship("User", back_populates="chat_sessions")