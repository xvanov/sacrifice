import hashlib
import os
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media_upload import MediaUpload
from app.config import settings


def resolve_upload_path(user_id: uuid.UUID, upload_id: uuid.UUID, goal_id: uuid.UUID | None = None) -> Path:
    """Resolve the on-disk path for an uploaded video file."""
    goal_or_orphan = str(goal_id) if goal_id else "orphan"
    return Path(settings.media_dir) / str(user_id) / goal_or_orphan / f"{upload_id}.mp4"


async def persist_upload(
    db: AsyncSession,
    *,
    file_bytes: bytes,
    mime_type: str,
    duration_seconds: float,
    user_id: uuid.UUID,
    upload_id: uuid.UUID,
    goal_id: uuid.UUID | None = None,
) -> MediaUpload:
    """Write the uploaded file to disk and persist metadata."""
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    storage_path_obj = resolve_upload_path(user_id, upload_id, goal_id)

    storage_path_obj.parent.mkdir(parents=True, exist_ok=True)
    storage_path_obj.write_bytes(file_bytes)

    upload = MediaUpload(
        id=upload_id,
        user_id=user_id,
        goal_id=goal_id,
        sha256=sha256,
        size_bytes=len(file_bytes),
        duration_seconds=duration_seconds,
        mime_type=mime_type,
        storage_path=str(storage_path_obj),
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)
    return upload