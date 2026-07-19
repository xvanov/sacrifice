import re
from datetime import datetime

from pydantic import BaseModel, field_validator

YOUTUBE_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w-]{11}"
)


class YouTubeProofSubmission(BaseModel):
    youtube_url: str

    @field_validator("youtube_url")
    @classmethod
    def validate_youtube_url(cls, v):
        if not YOUTUBE_URL_PATTERN.match(v):
            raise ValueError("Invalid YouTube URL")
        return v


class ApiEndpointProofSubmission(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] | None = None
    expected_status: int | None = None
    expected_body_schema: dict | None = None


class DevSandboxProofSubmission(BaseModel):
    repo_url: str
    branch: str = "main"
    test_command: str = "python -m pytest -v"
    language: str | None = None
    env_vars: dict[str, str] | None = None


class GithubRepoProofSubmission(BaseModel):
    repo_url: str
    branch: str = "main"
    github_token: str | None = None


class GeolocationProofSubmission(BaseModel):
    latitude: float
    longitude: float
    accuracy_m: float | None = None

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, v):
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, v):
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v


class ProofSubmissionCreate(BaseModel):
    youtube_url: str | None = None
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    expected_status: int | None = None
    expected_body_schema: dict | None = None
    repo_url: str | None = None
    branch: str | None = None
    test_command: str | None = None
    language: str | None = None
    env_vars: dict[str, str] | None = None
    github_token: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy_m: float | None = None


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
