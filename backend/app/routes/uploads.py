import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.media_upload import MediaUpload
from app.models.user import User

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.get("/{upload_id}")
async def get_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        uid = uuid.UUID(upload_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    result = await db.execute(
        select(MediaUpload).where(MediaUpload.id == uid)
    )
    upload = result.scalar_one_or_none()

    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if str(upload.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    return {
        "upload_id": str(upload.id),
        "goal_id": str(upload.goal_id) if upload.goal_id else None,
        "sha256": upload.sha256,
        "size_bytes": upload.size_bytes,
        "duration_seconds": upload.duration_seconds,
        "mime_type": upload.mime_type,
        "created_at": upload.created_at.isoformat(),
    }