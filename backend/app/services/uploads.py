import hashlib
import os
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.upload import MediaUpload


def _resolve_storage_path(*, user_id: str, goal_id: str | None, upload_id: str) -> Path:
    """Resolve the on-disk path for an upload.

    Convention: <media_dir>/<user_id>/<goal_or_orphan>/<upload_id>.mp4
    """
    segment = goal_id if goal_id else "orphan"
    return Path(settings.media_dir) / user_id / segment / f"{upload_id}.mp4"


async def write_upload(
    *,
    db: AsyncSession,
    user_id: str,
    file_content: bytes,
    mime_type: str,
    duration_seconds: float,
    goal_id: str | None = None,
) -> MediaUpload:
    """Write an uploaded video to disk and persist metadata.

    Returns the persisted MediaUpload row.
    """
    sha256 = hashlib.sha256(file_content).hexdigest()
    size_bytes = len(file_content)
    upload_id = str(uuid.uuid4())

    storage_path = _resolve_storage_path(
        user_id=user_id, goal_id=goal_id, upload_id=upload_id
    )
    os.makedirs(storage_path.parent, exist_ok=True)
    storage_path.write_bytes(file_content)

    upload = MediaUpload(
        id=upload_id,
        user_id=user_id,
        goal_id=goal_id,
        sha256=sha256,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        mime_type=mime_type,
        storage_path=str(storage_path),
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)
    return upload