import hashlib
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.upload import MediaUpload
from app.models.user import User


def _resolve_storage_path(user_id: uuid.UUID, goal_id: uuid.UUID | None, upload_id: uuid.UUID) -> Path:
    subdir = str(goal_id) if goal_id else "orphan"
    return Path(settings.media_dir) / str(user_id) / subdir / f"{upload_id}.mp4"


async def write_upload(
    db: AsyncSession,
    user: User,
    file_bytes: bytes,
    duration_seconds: float,
    goal_id: uuid.UUID | None,
) -> MediaUpload:
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    size_bytes = len(file_bytes)

    upload = MediaUpload(
        user_id=user.id,
        goal_id=goal_id,
        sha256=sha256,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        mime_type="video/mp4",
        storage_path="",  # populated after path resolution
    )
    db.add(upload)
    await db.flush()  # ensure upload.id is available

    storage_path = _resolve_storage_path(user.id, goal_id, upload.id)
    os.makedirs(storage_path.parent, exist_ok=True)
    with open(storage_path, "wb") as f:
        f.write(file_bytes)

    upload.storage_path = str(storage_path)
    await db.commit()
    await db.refresh(upload)
    return upload


async def get_upload_by_id(
    db: AsyncSession,
    upload_id: uuid.UUID,
) -> MediaUpload | None:
    result = await db.execute(select(MediaUpload).where(MediaUpload.id == upload_id))
    return result.scalar_one_or_none()