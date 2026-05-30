import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.user import User
from app.schemas.upload import UploadDetailResponse, UploadResponse
from app.services.uploads import get_upload_by_id, write_upload

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_CONTENT_TYPES = {"video/mp4", "video/quicktime"}


@router.post("/video", status_code=status.HTTP_201_CREATED, response_model=UploadResponse)
async def upload_video(
    file: UploadFile,
    duration_seconds: float = Form(...),
    goal_id: uuid.UUID | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {file.content_type}",
        )

    file_bytes = await file.read()
    if len(file_bytes) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum upload size",
        )

    if goal_id:
        result = await db.execute(
            select(Goal).where(Goal.id == goal_id, Goal.user_id == current_user.id)
        )
        goal = result.scalar_one_or_none()
        if goal is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Goal not found or not owned by current user",
            )

    upload = await write_upload(
        db=db,
        user=current_user,
        file_bytes=file_bytes,
        duration_seconds=duration_seconds,
        goal_id=goal_id,
    )

    return UploadResponse(
        upload_id=upload.id,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
        duration_seconds=upload.duration_seconds,
        mime_type=upload.mime_type,
    )


@router.get("/{upload_id}", response_model=UploadDetailResponse)
async def get_upload(
    upload_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    upload = await get_upload_by_id(db, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload not found",
        )
    if str(upload.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Upload not owned by current user",
        )
    return UploadDetailResponse(
        upload_id=upload.id,
        goal_id=upload.goal_id,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
        duration_seconds=upload.duration_seconds,
        mime_type=upload.mime_type,
        created_at=upload.created_at,
    )