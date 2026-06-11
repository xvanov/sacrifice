import asyncio
import hashlib
import os
import uuid
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.media import MediaUpload


def resolve_upload_path(
    user_id: uuid.UUID,
    goal_id: uuid.UUID | None,
    upload_id: uuid.UUID,
    media_root: Path | None = None,
) -> Path:
    """Resolve the storage path for an upload, keyed by (user_id, goal_id_or_orphan, upload_id)."""
    if media_root is None:
        media_root = Path(settings.sacrifice_media_dir)
    goal_segment = str(goal_id) if goal_id is not None else "orphan"
    return media_root / str(user_id) / goal_segment / f"{upload_id}.mp4"


def compute_sha256(content: bytes) -> str:
    """Compute the SHA-256 hex digest of the given bytes."""
    return hashlib.sha256(content).hexdigest()


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    return compute_sha256(path.read_bytes())


def _unlink_if_exists(path: Path) -> None:
    """Remove a file if it exists (safe for use in thread executor)."""
    path.unlink(missing_ok=True)


def _remove_empty_ancestors(leaf: Path, root: Path) -> None:
    """Remove empty parent directories from *leaf* up to (but excluding) *root*."""
    for parent in leaf.parents:
        if parent == root or not parent.is_relative_to(root):
            break
        try:
            parent.rmdir()
        except OSError:
            break


class UploadService:
    """Encapsulates path resolution, write, hash computation, and metadata persistence."""

    def __init__(self, media_root: Path | None = None) -> None:
        self.media_root = media_root if media_root is not None else Path(settings.sacrifice_media_dir)

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
        """Orchestrate path resolution, write, hash, and persistence.

        Writes content to a temporary file first, persists metadata, then
        atomically renames to the final destination.  If the process dies
        before the rename the temp file is harmless (no metadata references
        it); if it dies after the rename both the file and the DB row exist.
        """
        upload_id = uuid.uuid4()
        dest_path = resolve_upload_path(user_id, goal_id, upload_id, self.media_root)
        media_root = self.media_root  # capture for closure

        # Write to a temp file in the same directory so the atomic rename
        # is on the same filesystem (os.rename is atomic on POSIX).
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + f".tmp-{os.getpid()}-{upload_id.hex}")
        await asyncio.to_thread(tmp_path.write_bytes, content)
        sha256 = await asyncio.to_thread(_hash_file, tmp_path)

        # Register rollback/commit cleanup BEFORE persist so they cover the
        # eventual destination path even if the temp→final rename already ran.
        def _on_rollback(session_: object) -> None:
            _unlink_if_exists(dest_path)
            _remove_empty_ancestors(dest_path, media_root)

        def _on_commit(session_: object) -> None:
            event.remove(session_, "after_rollback", _on_rollback)

        event.listen(session.sync_session, "after_rollback", _on_rollback, once=True)
        event.listen(session.sync_session, "after_commit", _on_commit, once=True)

        try:
            result = await self.persist_metadata(
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
        except Exception:
            # Metadata persistence failed.  The rollback listener above
            # handles cleaning up dest_path if anything was flushed;
            # also clean up the temp file on failure.
            await asyncio.to_thread(_unlink_if_exists, tmp_path)
            await session.rollback()
            raise

        # Metadata persisted — atomically promote the temp file.
        await asyncio.to_thread(os.rename, tmp_path, dest_path)

        return result

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
        """Insert a row into media_uploads and return the persisted MediaUpload.

        Uses flush so the caller owns transaction boundaries (commit/rollback).
        """
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
        await session.flush()
        await session.refresh(upload)
        return upload