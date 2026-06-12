import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal
from app.models.user import User
from app.schemas.upload import UploadDetailResponse, UploadResponse
from app.services.uploads import get_upload_by_id, write_upload

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_MIME_TYPES = frozenset({"video/mp4", "video/quicktime"})


@router.post("/video", status_code=status.HTTP_201_CREATED, response_model=UploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    duration_seconds: float = Form(...),
    goal_id: uuid.UUID | None = Form(None),
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
        if goal is None or goal.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    try:
        upload = await write_upload(
            db=db,
            user_id=current_user.id,
            file=file,
            mime_type=file.content_type,
            duration_seconds=duration_seconds,
            goal_id=goal_id,
        )
    except ValueError as e:
        if str(e) == "file_exceeds_max_size":
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="File exceeds maximum upload size",
            )
        raise

    return {
        "upload_id": upload.id,
        "sha256": upload.sha256,
        "size_bytes": upload.size_bytes,
        "duration_seconds": upload.duration_seconds,
        "mime_type": upload.mime_type,
    }


@router.get("/{upload_id}", response_model=UploadDetailResponse)
async def get_upload(
    upload_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    upload = await get_upload_by_id(db=db, upload_id=upload_id)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if upload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    return {
        "upload_id": upload.id,
        "goal_id": upload.goal_id,
        "sha256": upload.sha256,
        "size_bytes": upload.size_bytes,
        "duration_seconds": upload.duration_seconds,
        "mime_type": upload.mime_type,
        "created_at": upload.created_at,
    }