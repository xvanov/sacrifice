"""
Unit tests for backend/app/services/uploads.py

Covers the story's acceptance criteria: path resolution keyed by
(user_id, goal_id_or_unassigned, upload_id), file write with directory
creation, SHA-256 hashing, metadata persistence, and cleanup on failure.

Every test asserts an observable outcome of the service API.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from app.config import settings
from app.models.goal import Goal
from app.models.media import MediaUpload
from app.models.user import User
from app.services.uploads import UploadService, compute_sha256, resolve_upload_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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

        # Assert the exact relative path segments under the explicit root:
        #   <custom>/<user_id>/orphan/<upload_id>.mp4
        assert path == custom / str(user_id) / "orphan" / f"{upload_id}.mp4"


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
    @pytest.mark.asyncio
    async def test_explicit_root_overrides_config_default(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        """UploadService with an explicit media_root resolves paths and
        persists files under that root, not the config default."""
        explicit = tmp_path / "explicit-root"
        service = UploadService(media_root=explicit)

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"explicit-root-test",
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        await _engine_session.commit()

        stored = Path(result.storage_path)
        # The stored path must be under the explicit root, not the config default.
        assert explicit in stored.parents
        assert stored.exists()
        assert stored.read_bytes() == b"explicit-root-test"


# =============================================================================
# save_upload — behavioural tests against real DB
# =============================================================================


@pytest_asyncio.fixture
async def _engine_session():
    """Yield an AsyncSession connected to the test database."""
    engine = create_async_engine(settings.database_url, echo=False)
    test_async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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
        deadline=datetime(2026, 6, 1, tzinfo=UTC),
    )
    _engine_session.add(goal)
    await _engine_session.commit()
    await _engine_session.refresh(goal)
    return goal


class TestSaveUploadHappyPath:
    """save_upload persists file bytes to disk and metadata to DB."""

    @pytest.mark.asyncio
    async def test_persists_file_at_correct_goal_scoped_path(
        self,
        _engine_session: AsyncSession,
        _user: User,
        _goal: Goal,
        tmp_path: Path,
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
    async def test_persists_metadata_fields(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        """save_upload persists sha256, size_bytes, and duration_seconds correctly."""
        service = UploadService(media_root=tmp_path)
        content = b"combined-metadata-test" * 77
        duration = 12.5

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=content,
            duration_seconds=duration,
            mime_type="video/mp4",
        )
        await _engine_session.commit()

        expected_digest = hashlib.sha256(content).hexdigest()
        assert result.sha256 == expected_digest
        assert result.size_bytes == len(content)
        assert result.duration_seconds == duration

        row = await _engine_session.get(MediaUpload, result.id)
        assert row is not None
        assert row.sha256 == expected_digest
        assert row.size_bytes == len(content)
        assert row.duration_seconds == duration

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mime", ["video/mp4", "video/quicktime"])
    async def test_persists_metadata_with_correct_mime_type(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path, mime: str
    ):
        service = UploadService(media_root=tmp_path)

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
    async def test_creates_media_root_directory_if_needed(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        nonexistent = tmp_path / "nested" / "media-root"
        # Parent directory tree must NOT exist before the call.
        assert not nonexistent.exists(), (
            f"media root must not exist before save_upload: {nonexistent}"
        )

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
        # The full parent tree was created: media root, user dir, orphan dir.
        assert nonexistent.exists()
        assert (nonexistent / str(_user.id)).exists()
        assert (nonexistent / str(_user.id) / "orphan").exists()


# =============================================================================
# save_upload — cleanup on failure
# =============================================================================


class TestSaveUploadCleanupOnFailure:
    """When the caller rolls back after save_upload, no files or DB rows
    may remain — the transaction boundary is the atomicity guarantee.
    """

    @pytest.mark.asyncio
    async def test_persist_metadata_failure_after_flush_rolls_back_and_cleans_up(
        self,
        _engine_session: AsyncSession,
        _user: User,
        tmp_path: Path,
    ):
        """If persist_metadata raises after flush, the session is rolled back
        so no media_uploads row survives, and temp files are cleaned up."""

        real_service = UploadService(media_root=tmp_path)

        class FailingService(UploadService):
            async def persist_metadata(self, **kwargs):
                await real_service.persist_metadata(**kwargs)
                raise RuntimeError("simulated post-flush failure")

        service = FailingService(media_root=tmp_path)
        user_id = _user.id

        with pytest.raises(RuntimeError, match="simulated post-flush failure"):
            await service.save_upload(
                session=_engine_session,
                user_id=user_id,
                goal_id=None,
                content=b"post-flush-failure-test",
                duration_seconds=1.0,
                mime_type="video/mp4",
            )

        # save_upload rolled back the session; create a fresh engine to
        # verify nothing was persisted.
        verify_engine = create_async_engine(settings.database_url, echo=False)
        fresh = async_sessionmaker(
            verify_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with fresh() as s:
            rows = (
                (await s.execute(select(MediaUpload).where(MediaUpload.user_id == user_id)))
                .scalars()
                .all()
            )
            assert len(rows) == 0, (
                f"media_uploads row survived post-flush persist_metadata failure: {rows}"
            )
        await verify_engine.dispose()

        # Neither temp nor final files may remain.
        orphan_dir = tmp_path / str(user_id) / "orphan"
        if orphan_dir.exists():
            all_files = list(orphan_dir.glob("*.mp4*"))
            assert len(all_files) == 0, f"Files left after persist_metadata failure: {all_files}"

    @pytest.mark.asyncio
    async def test_no_orphaned_file_after_caller_rollback(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        """After rollback: no final file, no DB row, no temp file remain."""
        service = UploadService(media_root=tmp_path)
        user_dir = tmp_path / str(_user.id)

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"rollback-test-content",
            duration_seconds=3.0,
            mime_type="video/mp4",
        )
        stored_path = Path(result.storage_path)
        # Final file exists before commit — rename happens in save_upload.
        assert stored_path.exists(), "Final file must exist after save_upload returns"

        await _engine_session.rollback()

        # DB row must be gone.
        row = await _engine_session.get(MediaUpload, result.id)
        assert row is None, "media_uploads row must be gone after rollback"

        # No files (final or temp) may remain.
        if user_dir.exists():
            survivors = list(user_dir.rglob("*"))
            assert len(survivors) == 0, f"Files left after rollback: {survivors}"

    @pytest.mark.asyncio
    async def test_no_empty_directories_after_caller_rollback(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        service = UploadService(media_root=tmp_path)

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"dir-cleanup-test",
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        stored_path = Path(result.storage_path)
        orphan_dir = stored_path.parent
        user_dir = orphan_dir.parent

        await _engine_session.rollback()

        assert not orphan_dir.exists(), f"Orphan directory not cleaned up: {orphan_dir}"
        assert not user_dir.exists(), f"Empty user directory not cleaned up: {user_dir}"

    @pytest.mark.asyncio
    async def test_temp_file_cleaned_up_on_rollback(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        """After save_upload returns, the final file exists and no temp file
        remains.  After rollback, neither final nor temp files survive."""
        service = UploadService(media_root=tmp_path)
        user_dir = tmp_path / str(_user.id)

        result = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"temp-cleanup-test",
            duration_seconds=1.0,
            mime_type="video/mp4",
        )

        # After save_upload, the rename has already happened.
        assert Path(result.storage_path).exists(), "Final file must exist after save_upload returns"
        temp_files = list((user_dir / "orphan").glob("*.tmp-*"))
        assert len(temp_files) == 0, f"Expected no temp files after save_upload, got: {temp_files}"

        await _engine_session.rollback()

        # After rollback: no temp files, no final files.
        if user_dir.exists():
            survivors = list(user_dir.rglob("*"))
            assert len(survivors) == 0, f"Files left after rollback: {survivors}"

    @pytest.mark.asyncio
    async def test_committed_upload_survives_unrelated_rollback_on_same_session(
        self, _engine_session: AsyncSession, _user: User, tmp_path: Path
    ):
        """A committed upload must not be deleted when the same session
        later rolls back an unrelated transaction — the after_commit
        listener must remove the rollback cleanup from the first upload."""
        service = UploadService(media_root=tmp_path)

        # Upload A — commit
        result_a = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"surviving-upload",
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        stored_a = Path(result_a.storage_path)
        result_a_id = result_a.id
        result_a_sha256 = result_a.sha256
        # Final file exists after save_upload — rename happens before return.
        assert stored_a.exists(), "Upload A file must exist after save_upload"
        await _engine_session.commit()
        assert stored_a.exists(), "Upload A file must exist after commit"

        # Save a second upload on the same session and roll it back.
        # This verifies that the after_commit listener from upload A
        # removed its rollback cleanup — upload A's file must survive
        # an unrelated rollback on the reused AsyncSession.
        result_b = await service.save_upload(
            session=_engine_session,
            user_id=_user.id,
            goal_id=None,
            content=b"rollback-me",
            duration_seconds=1.0,
            mime_type="video/mp4",
        )
        stored_b = Path(result_b.storage_path)
        assert stored_b.exists(), "Upload B file must exist after save_upload"
        await _engine_session.rollback()

        # Upload A's file must still exist — the after_commit listener
        # should have removed the rollback cleanup, so the unrelated
        # rollback does not delete a committed upload.
        assert stored_a.exists(), f"Committed upload deleted by unrelated rollback: {stored_a}"

        # Upload B's file must be gone after rollback.
        assert not stored_b.exists(), f"Rolled-back upload B final file left on disk: {stored_b}"

        # Also verify the DB row persisted
        row = await _engine_session.get(MediaUpload, result_a_id)
        assert row is not None, "Committed upload row must survive unrelated rollback"
        assert row.sha256 == result_a_sha256

    @pytest.mark.asyncio
    async def test_rename_failure_rolls_back_and_cleans_up(
        self,
        _engine_session: AsyncSession,
        _user: User,
        tmp_path: Path,
        monkeypatch,
    ):
        """If the temp→final rename fails, the session is rolled back and
        no DB row or file (temp or final) survives."""
        service = UploadService(media_root=tmp_path)
        user_id = _user.id

        def failing_rename(src, dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr("os.rename", failing_rename)

        with pytest.raises(OSError, match="simulated rename failure"):
            await service.save_upload(
                session=_engine_session,
                user_id=user_id,
                goal_id=None,
                content=b"rename-failure-test",
                duration_seconds=1.0,
                mime_type="video/mp4",
            )

        # Verify no DB row survived via a fresh engine.
        verify_engine = create_async_engine(settings.database_url, echo=False)
        fresh = async_sessionmaker(
            verify_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with fresh() as s:
            rows = (
                (await s.execute(select(MediaUpload).where(MediaUpload.user_id == user_id)))
                .scalars()
                .all()
            )
            assert len(rows) == 0, f"media_uploads row survived rename failure: {rows}"
        await verify_engine.dispose()

        # Neither temp nor final files may remain.
        orphan_dir = tmp_path / str(user_id) / "orphan"
        if orphan_dir.exists():
            all_files = list(orphan_dir.glob("*.mp4*"))
            assert len(all_files) == 0, f"Files left after rename failure: {all_files}"
