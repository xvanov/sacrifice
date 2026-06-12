import hashlib
import os
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.upload import MediaUpload

ORPHAN_PATH_SEGMENT = "unassigned"

def _resolve_storage_path(
    *, user_id: uuid.UUID, goal_id: uuid.UUID | None, upload_id: uuid.UUID, mime_type: str
) -> Path:
    """Resolve the on-disk path for an upload.

    Convention: <media_dir>/<user_id>/<goal_or_orphan>/<upload_id>.mp4
    """
    segment = str(goal_id) if goal_id else ORPHAN_PATH_SEGMENT
    return Path(settings.media_dir) / str(user_id) / segment / f"{upload_id}.mp4"


async def write_upload(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    file: UploadFile,
    mime_type: str,
    duration_seconds: float,
    goal_id: uuid.UUID | None = None,
) -> MediaUpload:
    """Stream an uploaded video to disk, compute hash, enforce size limit,
    and persist metadata.

    Returns the persisted MediaUpload row.
    """
    upload_id = uuid.uuid4()

    storage_path = _resolve_storage_path(
        user_id=user_id, goal_id=goal_id, upload_id=upload_id, mime_type=mime_type
    )
    os.makedirs(storage_path.parent, exist_ok=True)

    sha256 = hashlib.sha256()
    size_bytes = 0
    max_size = settings.max_upload_size_bytes

    with open(storage_path, "wb") as f:
        while chunk := await file.read(64 * 1024):  # 64 KiB chunks
            size_bytes += len(chunk)
            if size_bytes > max_size:
                f.close()
                os.unlink(storage_path)
                raise ValueError("file_exceeds_max_size")
            sha256.update(chunk)
            f.write(chunk)

    upload = MediaUpload(
        id=upload_id,
        user_id=user_id,
        goal_id=goal_id,
        sha256=sha256.hexdigest(),
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        mime_type=mime_type,
        storage_path=str(storage_path),
    )
    db.add(upload)
    try:
        await db.commit()
        await db.refresh(upload)
    except Exception:
        await db.rollback()
        os.unlink(storage_path)
        raise
    return upload


async def get_upload_by_id(
    *, db: AsyncSession, upload_id: uuid.UUID
) -> MediaUpload | None:
    """Retrieve an upload by id without user scoping.
    
    Callers must perform their own authorization checks.
    """
    from sqlalchemy import select

    result = await db.execute(select(MediaUpload).where(MediaUpload.id == upload_id))
    return result.scalar_one_or_none()


async def get_upload_for_user(
    *, db: AsyncSession, upload_id: uuid.UUID, user_id: uuid.UUID
) -> MediaUpload | None:
    """Retrieve an upload by id, scoped to the owning user."""
    from sqlalchemy import select

    result = await db.execute(
        select(MediaUpload).where(
            MediaUpload.id == upload_id, MediaUpload.user_id == user_id
        )
    )
    return result.scalar_one_or_none()