import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.media_upload import MediaUpload
from app.models.user import User
from app.schemas.uploads import VideoUploadResponse, UploadMetadataResponse
from app.services.uploads import persist_upload

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_MIME_TYPES = {"video/mp4", "video/quicktime"}


@router.post("/video", status_code=201, response_model=VideoUploadResponse)
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    duration_seconds: float = Form(...),
    goal_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413,
            detail="File exceeds configured max size",
        )

    if not file.content_type or file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported media type",
        )

    file_bytes = await file.read()
    if len(file_bytes) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413,
            detail="File exceeds configured max size",
        )

    goal_uuid = None
    if goal_id:
        goal_uuid = uuid.UUID(goal_id)
        # Verify goal exists and is owned by the authenticated user
        result = await db.execute(select(Goal).where(Goal.id == goal_uuid))
        goal = result.scalar_one_or_none()
        if not goal:
            raise HTTPException(
                status_code=403,
                detail="Goal not found or not owned by user",
            )
        if str(goal.user_id) != str(current_user.id):
            raise HTTPException(
                status_code=403,
                detail="Goal not found or not owned by user",
            )

    upload_id = uuid.uuid4()
    upload = await persist_upload(
        db,
        file_bytes=file_bytes,
        mime_type=file.content_type,
        duration_seconds=duration_seconds,
        user_id=current_user.id,
        upload_id=upload_id,
        goal_id=goal_uuid,
    )

    return VideoUploadResponse(
        upload_id=upload.id,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
        duration_seconds=upload.duration_seconds,
        mime_type=upload.mime_type,
    )


@router.get("/{upload_id}", response_model=UploadMetadataResponse)
async def get_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MediaUpload).where(MediaUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()

    if not upload:
        raise HTTPException(
            status_code=404,
            detail="Upload not found",
        )

    if str(upload.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    return UploadMetadataResponse(
        upload_id=upload.id,
        goal_id=upload.goal_id,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
        duration_seconds=upload.duration_seconds,
        mime_type=upload.mime_type,
        created_at=upload.created_at,
    )