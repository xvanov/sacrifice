from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.user import User
from app.services.uploads import write_upload

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_MIME_TYPES = frozenset({"video/mp4", "video/quicktime"})


@router.post("/video", status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    duration_seconds: float = Form(...),
    goal_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {file.content_type}. Allowed: video/mp4, video/quicktime",
        )

    if goal_id is not None:
        result = await db.execute(select(Goal).where(Goal.id == goal_id))
        goal = result.scalar_one_or_none()
        if goal is None or str(goal.user_id) != str(current_user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum upload size of {settings.max_upload_size_bytes} bytes",
        )

    upload = await write_upload(
        db=db,
        user_id=str(current_user.id),
        file_content=content,
        mime_type=file.content_type,
        duration_seconds=duration_seconds,
        goal_id=goal_id,
    )

    return {
        "upload_id": str(upload.id),
        "sha256": upload.sha256,
        "size_bytes": upload.size_bytes,
        "duration_seconds": upload.duration_seconds,
        "mime_type": upload.mime_type,
    }