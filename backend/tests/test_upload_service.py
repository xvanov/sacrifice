import hashlib
import io
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.goal import Goal
from app.models.upload import MediaUpload
from app.models.user import User
from app.services.uploads import _resolve_storage_path, write_upload


def _make_mp4_bytes() -> bytes:
    return (
        b"\x00\x00\x00\x20\x66\x74\x79\x70\x69\x73\x6f\x6d"
        b"\x00\x00\x02\x00\x69\x73\x6f\x6d\x69\x73\x6f\x32"
        b"\x6d\x70\x34\x31\x00\x00\x00\x08\x66\x72\x65\x65"
    )


# Reusable user IDs so tests can reference them
USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
GOAL_A = uuid.uuid4()


@pytest.fixture
def temp_media_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "media_dir", tmpdir)
        yield Path(tmpdir)


@pytest.fixture
async def db_session():
    """Standalone DB session for service unit tests — does not depend on
    the conftest autouse fixture or FastAPI dependency overrides."""
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # Seed users and a goal so FK constraints are satisfied.
        session.add_all([
            User(id=USER_A, email="a@svc.test", display_name="Svc User A",
                 auth_provider="google", auth_provider_id="svc-sub-a"),
            User(id=USER_B, email="b@svc.test", display_name="Svc User B",
                 auth_provider="google", auth_provider_id="svc-sub-b"),
        ])
        await session.flush()

        session.add(
            Goal(
                id=GOAL_A, user_id=USER_A,
                title="Test Goal", goal_type="api_endpoint",
                pledge_amount=500, deadline=datetime(2026, 12, 31, tzinfo=timezone.utc),
            ),
        )
        await session.commit()
        yield session

    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE {table.name} CASCADE"))
    await engine.dispose()


# ─── write_upload: persistence / metadata ───────────────────────────


async def test_write_upload_persists_file_and_metadata(db_session, temp_media_dir):
    """write_upload writes the file to disk and persists a MediaUpload row
    with correct hash, size, mime, duration, user, and storage path."""

    mp4_bytes = _make_mp4_bytes()
    expected_hash = hashlib.sha256(mp4_bytes).hexdigest()

    file = UploadFile(
        filename="proof.mp4",
        file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )

    upload = await write_upload(
        db=db_session,
        user_id=str(USER_A),
        file=file,
        mime_type="video/mp4",
        duration_seconds=42.75,
        goal_id=str(GOAL_A),
    )

    # Row fields
    assert upload.user_id == USER_A
    assert upload.goal_id == GOAL_A
    assert upload.sha256 == expected_hash
    assert upload.size_bytes == len(mp4_bytes)
    assert upload.duration_seconds == 42.75
    assert upload.mime_type == "video/mp4"

    # File on disk
    storage_path = Path(upload.storage_path)
    assert storage_path.exists()
    on_disk = storage_path.read_bytes()
    assert on_disk == mp4_bytes

    # Path convention
    assert str(storage_path).startswith(str(temp_media_dir))
    assert str(USER_A) in str(storage_path)
    assert str(GOAL_A) in str(storage_path)

    # Persisted in DB
    result = await db_session.execute(
        select(MediaUpload).where(MediaUpload.id == upload.id)
    )
    row = result.scalar_one()
    assert row.sha256 == expected_hash


# ─── write_upload: orphan path segment ──────────────────────────────


async def test_write_upload_uses_orphan_path_segment_when_goal_is_absent(
    db_session, temp_media_dir,
):
    """When goal_id is None, the storage path uses the 'orphan' segment."""

    mp4_bytes = _make_mp4_bytes()

    file = UploadFile(
        filename="orphan_proof.mp4",
        file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )

    upload = await write_upload(
        db=db_session,
        user_id=str(USER_B),
        file=file,
        mime_type="video/mp4",
        duration_seconds=10.0,
        goal_id=None,
    )

    assert upload.goal_id is None
    storage_path = Path(upload.storage_path)
    assert storage_path.exists()
    assert "orphan" in str(storage_path)
    assert str(USER_B) in str(storage_path)


# ─── write_upload: extension in storage path ────────────────────────


async def test_write_upload_uses_mov_extension_for_quicktime(
    db_session, temp_media_dir,
):
    """write_upload for video/quicktime persists a storage_path ending in .mov."""
    mp4_bytes = _make_mp4_bytes()

    file = UploadFile(
        filename="proof.mov",
        file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/quicktime"},
    )

    upload = await write_upload(
        db=db_session,
        user_id=str(USER_A),
        file=file,
        mime_type="video/quicktime",
        duration_seconds=30.0,
        goal_id=str(GOAL_A),
    )

    storage_path = Path(upload.storage_path)
    assert storage_path.suffix == ".mov"
    assert storage_path.exists()


async def test_write_upload_uses_mp4_extension_for_mp4(
    db_session, temp_media_dir,
):
    """write_upload for video/mp4 persists a storage_path ending in .mp4."""
    mp4_bytes = _make_mp4_bytes()

    file = UploadFile(
        filename="proof.mp4",
        file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )

    upload = await write_upload(
        db=db_session,
        user_id=str(USER_A),
        file=file,
        mime_type="video/mp4",
        duration_seconds=15.0,
        goal_id=None,
    )

    storage_path = Path(upload.storage_path)
    assert storage_path.suffix == ".mp4"
    assert storage_path.exists()


def test_resolve_storage_path_orphan_segment_when_goal_is_none():
    path = _resolve_storage_path(
        user_id="u",
        goal_id=None,
        upload_id="up",
        mime_type="video/mp4",
    )
    assert "orphan" in str(path)
    assert "None" not in str(path)


# ─── write_upload: size enforcement ─────────────────────────────────


async def test_write_upload_raises_value_error_when_file_exceeds_limit(
    db_session, temp_media_dir, monkeypatch,
):
    """write_upload raises ValueError('file_exceeds_max_size') when the
    streamed file exceeds the configured limit."""
    monkeypatch.setattr(settings, "max_upload_size_bytes", 64)

    big_data = b"x" * 256

    file = UploadFile(
        filename="big.mp4",
        file=io.BytesIO(big_data),
        headers={"content-type": "video/mp4"},
    )

    with pytest.raises(ValueError, match="file_exceeds_max_size"):
        await write_upload(
            db=db_session,
            user_id=str(USER_A),
            file=file,
            mime_type="video/mp4",
            duration_seconds=1.0,
        )