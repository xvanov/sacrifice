from pydantic import BaseModel


class MediaUploadResponse(BaseModel):
    upload_id: str
    goal_id: str | None
    sha256: str
    size_bytes: int
    duration_seconds: float | None
    mime_type: str
    created_at: str