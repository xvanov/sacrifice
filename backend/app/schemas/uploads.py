import uuid
from datetime import datetime

from pydantic import BaseModel


class VideoUploadResponse(BaseModel):
    upload_id: uuid.UUID
    sha256: str
    size_bytes: int
    duration_seconds: float
    mime_type: str


class UploadMetadataResponse(BaseModel):
    upload_id: uuid.UUID
    goal_id: uuid.UUID | None
    sha256: str
    size_bytes: int
    duration_seconds: float
    mime_type: str
    created_at: datetime