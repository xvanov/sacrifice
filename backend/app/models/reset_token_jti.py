"""Track consumed password-reset token JTIs for single-use enforcement.

Each row records one consumed token JTI. The DB-level unique constraint on
(jti) prevents replay, and the timestamp enables periodic cleanup of rows
older than the token TTL.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ResetTokenJti(Base):
    __tablename__ = "reset_token_jtis"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    jti: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
