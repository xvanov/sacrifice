import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class AuthSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    access_jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    refresh_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replaced_by_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id"), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship("User", back_populates="auth_sessions")
