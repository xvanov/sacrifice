from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import Settings
from app.models.base import Base, UUIDMixin


def media_storage_path(
    user_id: uuid.UUID,
    goal_id: uuid.UUID | None,
    upload_id: uuid.UUID,
) -> str:
    """Return the configured storage path for a media upload.

    Convention: <root>/<user_id>/<goal_or_orphan>/<upload_id>.mp4
    """
    cfg = Settings()
    segment = cfg.sacrifice_media_orphan_segment if goal_id is None else str(goal_id)
    return f"{cfg.sacrifice_media_dir}/{user_id}/{segment}/{upload_id}.mp4"


class MediaUpload(UUIDMixin, Base):
    __tablename__ = "media_uploads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=True
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User")
    goal = relationship("Goal")