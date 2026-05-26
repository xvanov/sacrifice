import hashlib
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.media_upload import MediaUpload


def resolve_upload_path(
    user_id: uuid.UUID,
    goal_id: uuid.UUID | None,
    upload_id: uuid.UUID,
) -> Path:
    """Resolve the storage path for an upload, keyed by (user_id, goal_id_or_orphan, upload_id)."""
    goal_segment = str(goal_id) if goal_id is not None else "orphan"
    return Path(settings.media_dir) / str(user_id) / goal_segment / f"{upload_id}.mp4"


def compute_sha256(content: bytes) -> str:
    """Compute the SHA-256 hex digest of the given bytes."""
    return hashlib.sha256(content).hexdigest()


class UploadService:
    """Encapsulates path resolution, write, hash computation, and metadata persistence."""

    async def save_upload(
        self,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        goal_id: uuid.UUID | None,
        content: bytes,
        duration_seconds: float,
        mime_type: str,
    ) -> MediaUpload:
        """Orchestrate path resolution, write, hash, and persistence in one call.

        This is the primary entry point for route handlers — a single call that
        does everything needed to persist an upload and return its metadata row.
        """
        upload_id = uuid.uuid4()
        dest_path = resolve_upload_path(user_id, goal_id, upload_id)
        sha256 = compute_sha256(content)
        self.write_upload(dest_path, content)

        return await self.persist_metadata(
            session=session,
            upload_id=upload_id,
            user_id=user_id,
            goal_id=goal_id,
            sha256=sha256,
            size_bytes=len(content),
            duration_seconds=duration_seconds,
            mime_type=mime_type,
            storage_path=dest_path,
        )

    def write_upload(self, dest_path: Path, content: bytes) -> Path:
        """Write bytes to dest_path, creating parent directories as needed."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(content)
        return dest_path

    async def persist_metadata(
        self,
        *,
        session: AsyncSession,
        upload_id: uuid.UUID,
        user_id: uuid.UUID,
        goal_id: uuid.UUID | None,
        sha256: str,
        size_bytes: int,
        duration_seconds: float,
        mime_type: str,
        storage_path: Path,
    ) -> MediaUpload:
        """Insert a row into media_uploads and return the persisted MediaUpload."""
        upload = MediaUpload(
            id=upload_id,
            user_id=user_id,
            goal_id=goal_id,
            sha256=sha256,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
            mime_type=mime_type,
            storage_path=str(storage_path),
        )
        session.add(upload)
        await session.commit()
        return upload