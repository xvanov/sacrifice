import re
from datetime import datetime

from pydantic import BaseModel, field_validator


YOUTUBE_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w-]{11}"
)


class ProofSubmissionCreate(BaseModel):
    youtube_url: str

    @field_validator("youtube_url")
    @classmethod
    def validate_youtube_url(cls, v):
        if not YOUTUBE_URL_PATTERN.match(v):
            raise ValueError("Invalid YouTube URL")
        return v


class ProofSubmissionResponse(BaseModel):
    submission_id: str
    goal_id: str
    submitted_at: datetime
    verification_status: str
    verification_details: dict | None = None


class VerificationStatusResponse(BaseModel):
    submission_id: str
    verification_status: str
    verification_details: dict | None = None
