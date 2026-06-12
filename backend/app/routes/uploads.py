"""Routes for media upload endpoints."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.user import User
from app.services.uploads import UploadService

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_MIME_TYPES = {"video/mp4", "video/quicktime"}


@router.post("/video", status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    duration_seconds: float = Form(...),
    goal_id: uuid.UUID | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate media type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    # Validate goal ownership when goal_id is provided.
    # Per the error contract (operator note), the error set is closed at
    # {401, 403, 413, 415, 422}. A nonexistent goal_id returns 403 (treated
    # as not-owned to avoid leaking goal ids) — not 404.
    if goal_id is not None:
        goal = await db.get(Goal, goal_id)
        if goal is None or goal.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Goal not owned by authenticated user")

    # Read file content
    content = await file.read()

    # Enforce max upload size
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail="File exceeds configured max size")

    # Delegate to service
    service = UploadService()
    result = await service.save_upload(
        session=db,
        user_id=current_user.id,
        goal_id=goal_id,
        content=content,
        duration_seconds=duration_seconds,
        mime_type=file.content_type,
    )
    await db.commit()

    return {
        "upload_id": str(result.id),
        "sha256": result.sha256,
        "size_bytes": result.size_bytes,
        "duration_seconds": result.duration_seconds,
        "mime_type": result.mime_type,
    }