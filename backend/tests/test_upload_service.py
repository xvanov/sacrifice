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
from app.services.uploads import (
    _resolve_storage_path,
    get_upload_by_id,
    get_upload_for_user,
    write_upload,
)


def _make_mp4_bytes() -> bytes:
    """Minimal valid MP4 bytes (ftyp box)."""
    return (
        b"\x00\x00\x00\x20\x66\x74\x79\x70\x69\x73\x6f\x6d"
        b"\x00\x00\x02\x00\x69\x73\x6f\x6d\x69\x73\x6f\x32"
        b"\x6d\x70\x34\x31\x00\x00\x00\x08\x66\x72\x65\x65"
    )


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
    """Standalone DB session with seeded users and a goal for FK constraints."""
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
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


# ─── SACRIFICE_MEDIA_DIR config ─────────────────────────────────────


def test_media_dir_env_var_controls_storage_root():
    """SACRIFICE_MEDIA_DIR env var controls the resolved storage path root."""
    from app.config import settings
    uid = uuid.uuid4()
    gid = uuid.uuid4()
    upid = uuid.uuid4()
    path = _resolve_storage_path(
        user_id=uid, goal_id=gid, upload_id=upid, mime_type="video/mp4",
    )
    assert str(path).startswith(settings.media_dir)


# ─── _resolve_storage_path ──────────────────────────────────────────


def test_resolve_storage_path_uses_goal_segment_when_goal_id_present():
    uid = uuid.uuid4()
    gid = uuid.uuid4()
    upid = uuid.uuid4()
    path = _resolve_storage_path(
        user_id=uid, goal_id=gid, upload_id=upid, mime_type="video/mp4",
    )
    assert str(gid) in str(path)
    assert "unassigned" not in str(path)
    assert str(path).endswith(".mp4")


def test_resolve_storage_path_uses_unassigned_segment_when_goal_is_none():
    uid = uuid.uuid4()
    upid = uuid.uuid4()
    path = _resolve_storage_path(
        user_id=uid, goal_id=None, upload_id=upid, mime_type="video/mp4",
    )
    assert "unassigned" in str(path)
    assert "None" not in str(path)


def test_resolve_storage_path_uses_mov_extension_for_quicktime():
    uid = uuid.uuid4()
    gid = uuid.uuid4()
    upid = uuid.uuid4()
    path = _resolve_storage_path(
        user_id=uid, goal_id=gid, upload_id=upid, mime_type="video/quicktime",
    )
    assert str(path).endswith(".mov")


# ─── write_upload: successful persistence ───────────────────────────


async def test_write_upload_persists_metadata_and_file_for_owned_goal(
    db_session, temp_media_dir,
):
    mp4_bytes = _make_mp4_bytes()
    expected_hash = hashlib.sha256(mp4_bytes).hexdigest()

    file = UploadFile(
        filename="proof.mp4",
        file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )

    upload = await write_upload(
        db=db_session, user_id=USER_A, file=file,
        mime_type="video/mp4", duration_seconds=42.75, goal_id=GOAL_A,
    )

    assert upload.user_id == USER_A
    assert upload.goal_id == GOAL_A
    assert upload.sha256 == expected_hash
    assert upload.size_bytes == len(mp4_bytes)
    assert upload.duration_seconds == 42.75
    assert upload.mime_type == "video/mp4"

    storage_path = Path(upload.storage_path)
    assert storage_path.exists()
    assert storage_path.read_bytes() == mp4_bytes
    assert str(storage_path).startswith(str(temp_media_dir))
    assert str(USER_A) in str(storage_path)
    assert str(GOAL_A) in str(storage_path)

    # Verify row is actually in the database
    result = await db_session.execute(
        select(MediaUpload).where(MediaUpload.id == upload.id)
    )
    row = result.scalar_one()
    assert row.sha256 == expected_hash


async def test_write_upload_persists_unassigned_when_goal_id_is_none(
    db_session, temp_media_dir,
):
    mp4_bytes = _make_mp4_bytes()

    file = UploadFile(
        filename="unassigned.mp4",
        file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )

    upload = await write_upload(
        db=db_session, user_id=USER_B, file=file,
        mime_type="video/mp4", duration_seconds=10.0, goal_id=None,
    )

    assert upload.goal_id is None
    storage_path = Path(upload.storage_path)
    assert storage_path.exists()
    assert "unassigned" in str(storage_path)
    assert str(USER_B) in str(storage_path)


async def test_write_upload_uses_mov_extension_for_quicktime_mime(
    db_session, temp_media_dir,
):
    mp4_bytes = _make_mp4_bytes()

    file = UploadFile(
        filename="proof.mov",
        file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/quicktime"},
    )

    upload = await write_upload(
        db=db_session, user_id=USER_A, file=file,
        mime_type="video/quicktime", duration_seconds=30.0, goal_id=GOAL_A,
    )

    assert Path(upload.storage_path).suffix == ".mov"
    assert Path(upload.storage_path).exists()


# ─── write_upload: size enforcement ─────────────────────────────────


async def test_write_upload_raises_and_cleans_up_when_file_exceeds_limit(
    db_session, temp_media_dir, monkeypatch,
):
    """Oversized files raise ValueError and leave no partial files on disk."""
    monkeypatch.setattr(settings, "max_upload_size_bytes", 64)

    big_data = b"x" * 256
    file = UploadFile(
        filename="big.mp4",
        file=io.BytesIO(big_data),
        headers={"content-type": "video/mp4"},
    )

    with pytest.raises(ValueError, match="file_exceeds_max_size"):
        await write_upload(
            db=db_session, user_id=USER_A, file=file,
            mime_type="video/mp4", duration_seconds=1.0,
        )

    # No media files should exist under temp_media_dir
    media_files = list(Path(str(temp_media_dir)).rglob("*"))
    assert not any(p.is_file() for p in media_files), \
        f"Partial files left behind: {[p for p in media_files if p.is_file()]}"


# ─── get_upload_by_id ───────────────────────────────────────────────


async def test_get_upload_by_id_returns_upload_when_it_exists(db_session, temp_media_dir):
    mp4_bytes = _make_mp4_bytes()
    file = UploadFile(
        filename="p.mp4", file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )
    created = await write_upload(
        db=db_session, user_id=USER_A, file=file,
        mime_type="video/mp4", duration_seconds=5.0,
    )

    found = await get_upload_by_id(db=db_session, upload_id=created.id)
    assert found is not None
    assert found.id == created.id
    assert found.sha256 == created.sha256


async def test_get_upload_by_id_returns_none_when_upload_does_not_exist(db_session):
    found = await get_upload_by_id(db=db_session, upload_id=uuid.uuid4())
    assert found is None


# ─── get_upload_for_user ────────────────────────────────────────────


async def test_get_upload_for_user_returns_upload_for_correct_user(
    db_session, temp_media_dir,
):
    mp4_bytes = _make_mp4_bytes()
    file = UploadFile(
        filename="p.mp4", file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )
    created = await write_upload(
        db=db_session, user_id=USER_A, file=file,
        mime_type="video/mp4", duration_seconds=5.0,
    )

    found = await get_upload_for_user(
        db=db_session, upload_id=created.id, user_id=USER_A,
    )
    assert found is not None
    assert found.id == created.id


async def test_get_upload_for_user_returns_none_for_wrong_user(
    db_session, temp_media_dir,
):
    mp4_bytes = _make_mp4_bytes()
    file = UploadFile(
        filename="p.mp4", file=io.BytesIO(mp4_bytes),
        headers={"content-type": "video/mp4"},
    )
    created = await write_upload(
        db=db_session, user_id=USER_A, file=file,
        mime_type="video/mp4", duration_seconds=5.0,
    )

    found = await get_upload_for_user(
        db=db_session, upload_id=created.id, user_id=USER_B,
    )
    assert found is None


async def test_get_upload_for_user_returns_none_when_upload_does_not_exist(db_session):
    found = await get_upload_for_user(
        db=db_session, upload_id=uuid.uuid4(), user_id=USER_A,
    )
    assert found is None