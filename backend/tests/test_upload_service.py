"""
Unit tests for upload service path resolution, file persistence, hash
computation, and metadata persistence.

These tests exercise the service layer in isolation — they do NOT go
through the HTTP layer or depend on a running backend.
"""

import hashlib
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.uploads import resolve_upload_path, persist_upload
from app.config import settings


# ---------------------------------------------------------------------------
# resolve_upload_path
# ---------------------------------------------------------------------------


def test_resolve_upload_path_with_goal_id():
    """Storage path is <media_dir>/<user_id>/<goal_id>/<upload_id>.mp4"""
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    upload_id = uuid.uuid4()

    result = resolve_upload_path(user_id, upload_id, goal_id)

    expected = Path(settings.media_dir) / str(user_id) / str(goal_id) / f"{upload_id}.mp4"
    assert result == expected


def test_resolve_upload_path_orphan_without_goal_id():
    """Orphan uploads go under 'orphan' subdirectory."""
    user_id = uuid.uuid4()
    upload_id = uuid.uuid4()

    result = resolve_upload_path(user_id, upload_id, goal_id=None)

    expected = Path(settings.media_dir) / str(user_id) / "orphan" / f"{upload_id}.mp4"
    assert result == expected


# ---------------------------------------------------------------------------
# persist_upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_upload_writes_file_and_persists_metadata(tmp_path):
    """persist_upload writes bytes to the resolved path and returns a
    persisted MediaUpload with correct metadata."""
    # Override media_dir to use a temp directory
    original = settings.media_dir
    settings.media_dir = str(tmp_path)
    try:
        db = AsyncMock()
        file_bytes = b"\x00\x01\x02\x03\x04\x05"
        mime_type = "video/mp4"
        duration_seconds = 7.5
        user_id = uuid.uuid4()
        upload_id = uuid.uuid4()
        goal_id = uuid.uuid4()

        result = await persist_upload(
            db,
            file_bytes=file_bytes,
            mime_type=mime_type,
            duration_seconds=duration_seconds,
            user_id=user_id,
            upload_id=upload_id,
            goal_id=goal_id,
        )

        # File written to the expected path
        expected_path = tmp_path / str(user_id) / str(goal_id) / f"{upload_id}.mp4"
        assert expected_path.exists()
        assert expected_path.read_bytes() == file_bytes

        # Metadata on the returned model
        assert result.id == upload_id
        assert result.user_id == user_id
        assert result.goal_id == goal_id
        assert result.sha256 == hashlib.sha256(file_bytes).hexdigest()
        assert result.size_bytes == len(file_bytes)
        assert result.duration_seconds == duration_seconds
        assert result.mime_type == mime_type
        assert result.storage_path == str(expected_path)

        # DB interactions
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()
    finally:
        settings.media_dir = original


@pytest.mark.asyncio
async def test_persist_upload_orphan_path_when_goal_id_is_none(tmp_path):
    """When goal_id is None, the file lands under the 'orphan' directory."""
    original = settings.media_dir
    settings.media_dir = str(tmp_path)
    try:
        db = AsyncMock()
        file_bytes = b"orphan-test-bytes"
        user_id = uuid.uuid4()
        upload_id = uuid.uuid4()

        result = await persist_upload(
            db,
            file_bytes=file_bytes,
            mime_type="video/quicktime",
            duration_seconds=3.0,
            user_id=user_id,
            upload_id=upload_id,
            goal_id=None,
        )

        expected_path = tmp_path / str(user_id) / "orphan" / f"{upload_id}.mp4"
        assert expected_path.exists()
        assert result.goal_id is None
        assert result.storage_path == str(expected_path)
    finally:
        settings.media_dir = original