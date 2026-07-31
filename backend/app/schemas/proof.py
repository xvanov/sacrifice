import re
from datetime import datetime

from pydantic import BaseModel, field_validator


YOUTUBE_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w-]{11}"
)

# Kept deliberately identical to ``OWNER_REPO_RE`` in app/workers/github_repo.py:
# this validator's whole purpose is to reject at the boundary exactly what the
# verifier would later be unable to parse. If that pattern changes, change this
# one in the same commit or the gap reopens.
_GITHUB_OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s#?]+)")


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
    # Optional: only needed for a private repo. Encrypted before it is stored —
    # see DevSandboxGoalType.submit_proof.
    github_token: str | None = None


class GithubRepoProofSubmission(BaseModel):
    repo_url: str
    branch: str = "main"
    github_token: str | None = None

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, v):
        """Reject a repo URL we cannot resolve to owner/name, at the boundary.

        This is a charge-integrity control, not tidiness. Accepting an
        unparseable value let it reach the verifier, which correctly reported "we
        cannot evaluate this" — a PERMANENT inconclusive reason. That saturates
        the retry budget immediately, flags the goal for operator review, and
        makes ``check_deadlines`` skip it on every subsequent sweep. Net effect:
        one request with ``{"repo_url": "not a url"}`` made any github_repo
        pledge permanently uncollectable, for free.

        "We accepted the input, so the failure is ours" is the right instinct in
        general, and it is exactly why this belongs here: the fix is to stop
        accepting it. A 422 tells the user to correct their submission and leaves
        the pledge enforceable, which is both honest and safe.
        """
        if not _GITHUB_OWNER_REPO_RE.search(v or ""):
            raise ValueError(
                "repo_url must be a GitHub repository URL of the form "
                "https://github.com/<owner>/<repo>"
            )
        return v


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
