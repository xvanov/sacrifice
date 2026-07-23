"""Password-reset token persistence.

Tokens are stored as SHA-256 hashes so a database leak does not give an
attacker valid reset tokens.  The raw token is returned to the caller
once (at creation time) and never persisted.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Lifetime of a reset token from issuance.
RESET_TOKEN_LIFETIME = timedelta(hours=1)


def generate_reset_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)`` for a new password-reset token."""
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


class PasswordResetToken(UUIDMixin, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
