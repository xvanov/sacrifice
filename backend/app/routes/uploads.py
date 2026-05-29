import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.media_upload import MediaUpload
from app.models.user import User
from app.services.uploads import UploadService

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_MIME_TYPES = {"video/mp4", "video/quicktime"}


@router.post("/video", status_code=status.HTTP_201_CREATED)
async def post_video_upload(
    file: UploadFile = File(...),
    duration_seconds: float = Form(...),
    goal_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {file.content_type}",
        )

    content = await file.read()
    if len(content) > settings.max_upload_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum upload size",
        )

    goal_uuid: uuid.UUID | None = None
    if goal_id:
        try:
            goal_uuid = uuid.UUID(goal_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid goal_id format",
            )

        result = await db.execute(
            select(Goal).where(Goal.id == goal_uuid)
        )
        goal = result.scalar_one_or_none()
        if goal is None or str(goal.user_id) != str(current_user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    service = UploadService()
    upload = await service.save_upload(
        session=db,
        user_id=current_user.id,
        goal_id=goal_uuid,
        content=content,
        duration_seconds=duration_seconds,
        mime_type=file.content_type,
    )

    return {
        "upload_id": str(upload.id),
        "sha256": upload.sha256,
        "size_bytes": upload.size_bytes,
        "duration_seconds": upload.duration_seconds,
        "mime_type": upload.mime_type,
    }


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