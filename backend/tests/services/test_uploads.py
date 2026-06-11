"""
Unit tests for backend/app/services/uploads.py

Covers the story's acceptance criteria: path resolution keyed by
(user_id, goal_id_or_unassigned, upload_id), file write with directory
creation, SHA-256 hashing, metadata persistence, and cleanup on failure.

Every test asserts an observable outcome of the service API.
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal
from app.models.media import MediaUpload
from app.models.user import User
from app.services.uploads import UploadService, compute_sha256, resolve_upload_path


# =============================================================================
# Path resolution
# =============================================================================


class TestResolveUploadPath:
    """resolve_upload_path produces:
    <media_root>/<user_id>/<goal_id_or_orphan>/<upload_id>.mp4
    """

    def test_goal_scoped_path_includes_goal_id(self, tmp_path: Path):
        user_id = uuid.uuid4()
        goal_id = uuid.uuid4()
        upload_id = uuid.uuid4()

        path = resolve_upload_path(user_id, goal_id, upload_id, media_root=tmp_path)

        assert path == tmp_path / str(user_id) / str(goal_id) / f"{upload_id}.mp4"

    def test_orphan_path_uses_orphan_segment(self, tmp_path: Path):
        user_id = uuid.uuid4()
        upload_id = uuid.uuid4()

        path = resolve_upload_path(user_id, None, upload_id, media_root=tmp_path)

        assert path == tmp_path / str(user_id) / "orphan" / f"{upload_id}.mp4"

    def test_explicit_media_root_is_respected(self, tmp_path: Path):
        custom = tmp_path / "custom-root"
        user_id = uuid.uuid4()
        upload_id = uuid.uuid4()

        path = resolve_upload_path(user_id, None, upload_id, media_root=custom)

        assert custom in path.parents
        # The path starts with the custom root
        assert path.parts[: len(custom.parts)] == custom.parts


# =============================================================================
# SHA-256 hashing
# =============================================================================


class TestComputeSha256:
    """compute_sha256 returns the correct hex digest for byte content."""

    def test_known_content_produces_expected_digest(self):
        content = b"hello world"
        expected = hashlib.sha256(content).hexdigest()

        assert compute_sha256(content) == expected

    def test_different_content_produces_different_digest(self):
        a = compute_sha256(b"alpha")
        b = compute_sha256(b"beta")

        assert a != b
        assert len(a) == 64
        assert len(b) == 64

    def test_empty_content_produces_correct_digest(self):
        expected = hashlib.sha256(b"").hexdigest()

        assert compute_sha256(b"") == expected


# =============================================================================
# File write (offline — no DB)
# =============================================================================


class TestWriteUpload:
    """write_upload creates parent directories and persists bytes to disk."""

    def test_creates_parent_directories_and_writes_bytes(self, tmp_path: Path):
        service = UploadService(media_root=tmp_path)
        dest = tmp_path / "a" / "b" / "c" / "test.bin"
        content = b"file-content"

        result = service.write_upload(dest, content)

        assert result == dest
        assert dest.exists()
        assert dest.read_bytes() == content

    def test_creates_parent_directories_when_they_already_exist(self, tmp_path: Path):
        service = UploadService(media_root=tmp_path)
        dest = tmp_path / "existing" / "file.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)

        service.write_upload(dest, b"data")

        assert dest.read_bytes() == b"data"

    def test_overwrites_existing_file(self, tmp_path: Path):
        service = UploadService(media_root=tmp_path)
        dest = tmp_path / "overwrite.bin"
        dest.write_bytes(b"original")

        service.write_upload(dest, b"replacement")

        assert dest.read_bytes() == b"replacement"


# =============================================================================
# UploadService — explicit media_root
# =============================================================================


class TestUploadServiceExplicitRoot:
    def test_explicit_root_overrides_config_default(self, tmp_path: Path):
        """UploadService accepts an explicit media_root independent of config."""
        explicit = tmp_path / "explicit-root"
        service = UploadService(media_root=explicit)
        assert service.media_root == explicit

        user_id = uuid.uuid4()
        upload_id = uuid.uuid4()
        path = resolve_upload_path(user_id, None, upload_id, media_root=explicit)
        assert explicit in path.parents


# =============================================================================
# save_upload — behavioural tests against real DB
# =============================================================================


@pytest_asyncio.fixture
async def _engine_session():
    """Yield an AsyncSession connected to the test database."""
    engine = create_async_engine(settings.database_url, echo=False)
    test_async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with test_async_session() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def _user(_engine_session: AsyncSession) -> User:
    user = User(
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Test User",
        auth_provider="test",
        auth_provider_id=f"test-{uuid.uuid4().hex[:8]}",
    )
    _engine_session.add(user)
    await _engine_session.commit()
    await _engine_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def _goal(_engine_session: AsyncSession, _user: User) -> Goal:
    goal = Goal(
        user_id=_user.id,
        title="Test Goal",
        goal_type="youtube_video",
        pledge_amount=500,
        deadline=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    _engine_session.add(goal)
    await _engine_session.commit()
    await _engine_session.refresh(goal)
    return goal


class TestSaveUploadHappyPath:
    """save_upload persists file bytes to disk and metadata to DB."""

    @pytest.mark.asyncio
    async def test_persists_file_at_correct_goal_scoped_path(
        self, _engine_session: AsyncSession, _user: User, _goal: Goal, tmp_path: Path,
    ):
        service = UploadService(media_root=tmp_path)
        content = b"goal-scoped content"

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=_goal.id,
            content=content,
            duration_seconds=3.0,
            mime_type="video/mp4",
        )
        await _engine_session.commit()

        stored = Path(result.storage_path)
        expected_dir = tmp_path / str(_user.id) / str(_goal.id)
        assert expected_dir == stored.parent
        assert stored.exists()
        assert stored.read_bytes() == content

    @pytest.mark.asyncio
    async def test_persists_file_at_correct_orphan_path(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)
        content = b"orphan content"

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=content,
            duration_seconds=1.5,
            mime_type="video/quicktime",
        )
        await _engine_session.commit()

        stored = Path(result.storage_path)
        assert stored.parent == tmp_path / str(_user.id) / "orphan"
        assert stored.exists()
        assert stored.read_bytes() == content

    @pytest.mark.asyncio
    async def test_persists_metadata_with_correct_sha256(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)
        content = b"sha256 test payload"

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=content,
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        await _engine_session.commit()

        expected_digest = hashlib.sha256(content).hexdigest()
        assert result.sha256 == expected_digest

        row = await _engine_session.get(MediaUpload, result.id)
        assert row is not None
        assert row.sha256 == expected_digest

    @pytest.mark.asyncio
    async def test_persists_metadata_with_correct_size_bytes(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)
        content = b"size-test" * 100

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=content,
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        await _engine_session.commit()

        assert result.size_bytes == len(content)

        row = await _engine_session.get(MediaUpload, result.id)
        assert row is not None
        assert row.size_bytes == len(content)

    @pytest.mark.asyncio
    async def test_persists_metadata_with_correct_mime_type(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)

        for mime in ("video/mp4", "video/quicktime"):
            result = await service.save_upload(
                session=_engine_session,
                user_id=_user.id,
                goal_id=None,
                content=b"mime-test",
                duration_seconds=1.0,
                mime_type=mime,
            )
            await _engine_session.commit()

            assert result.mime_type == mime
            row = await _engine_session.get(MediaUpload, result.id)
            assert row is not None
            assert row.mime_type == mime

    @pytest.mark.asyncio
    async def test_persists_metadata_with_correct_duration(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"duration-test",
            duration_seconds=12.5,
            mime_type="video/mp4",
        )
        await _engine_session.commit()

        assert result.duration_seconds == 12.5

        row = await _engine_session.get(MediaUpload, result.id)
        assert row is not None
        assert row.duration_seconds == 12.5

    @pytest.mark.asyncio
    async def test_creates_media_root_directory_if_needed(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        nonexistent = tmp_path / "nested" / "media-root"
        assert not nonexistent.exists()

        service = UploadService(media_root=nonexistent)

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"dir-creation test",
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        await _engine_session.commit()

        stored = Path(result.storage_path)
        assert stored.exists()


# =============================================================================
# save_upload — cleanup on failure
# =============================================================================


class TestSaveUploadCleanupOnFailure:
    """When the caller rolls back after save_upload, the on-disk file
    must be cleaned up so no orphaned files remain.
    """

    @pytest.mark.asyncio
    async def test_no_orphaned_file_after_caller_rollback(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)
        content = b"rollback-test-content"

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=content,
            duration_seconds=3.0,
            mime_type="video/mp4",
        )
        stored_path = Path(result.storage_path)
        assert stored_path.exists(), "File must exist immediately after save_upload"

        await _engine_session.rollback()

        row = await _engine_session.get(MediaUpload, result.id)
        assert row is None, "media_uploads row must be gone after rollback"
        assert not stored_path.exists(), (
            f"Orphaned file left on disk after rollback: {stored_path}"
        )

    @pytest.mark.asyncio
    async def test_no_empty_directories_after_caller_rollback(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)
        content = b"dir-cleanup-test"

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=content,
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        stored_path = Path(result.storage_path)
        orphan_dir = stored_path.parent
        user_dir = orphan_dir.parent

        await _engine_session.rollback()

        assert not orphan_dir.exists(), (
            f"Orphan directory not cleaned up: {orphan_dir}"
        )
        assert not user_dir.exists(), (
            f"Empty user directory not cleaned up: {user_dir}"
        )

    @pytest.mark.asyncio
    async def test_no_db_row_after_caller_rollback(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"db-row-test",
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        await _engine_session.rollback()

        row = await _engine_session.get(MediaUpload, result.id)
        assert row is None