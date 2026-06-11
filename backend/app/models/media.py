from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin


def media_storage_path(
    user_id: uuid.UUID,
    goal_id: uuid.UUID | None,
    upload_id: uuid.UUID,
    *,
    media_dir: str | None = None,
) -> Path:
    """Return the canonical storage path for a media upload.

    Convention: <media_dir>/<user_id>/<goal_or_orphan>/<upload_id>.mp4

    The orphan segment is controlled by
    ``settings.sacrifice_media_orphan_segment`` (default ``"orphan"``).
    """
    from app.config import settings

    root = Path(media_dir if media_dir is not None else settings.sacrifice_media_dir)
    goal_segment = str(goal_id) if goal_id is not None else settings.sacrifice_media_orphan_segment
    return root / str(user_id) / goal_segment / f"{upload_id}.mp4"


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