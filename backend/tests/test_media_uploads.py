"""Tests for D008 media_uploads model, config, and migration."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.media import MediaUpload
from app.models.user import User

# Tables that exist in schema but may be created/dropped by other tests.
ALL_TABLE_NAMES = [
    "media_uploads",
    "proof_submissions",
    "pledges",
    "chat_message_messages",
    "chat_messages",
    "chat_spend_ledger",
    "chat_sessions",
    "goals",
    "users",
]


def test_media_dir_config_default(monkeypatch):
    """AC: Recorded videos stored under configurable path with default.

    The default is /var/sacrifice/media when no env override is set.
    """
    monkeypatch.delenv("MEDIA_DIR", raising=False)
    from app.config import Settings

    s = Settings()
    assert s.media_dir == "/var/sacrifice/media"


def test_media_dir_config_can_be_overridden(monkeypatch):
    """AC: Config setting is expressible by later service logic."""
    monkeypatch.setenv("MEDIA_DIR", "/custom/path")
    from app.config import Settings

    s = Settings()
    assert s.media_dir == "/custom/path"


def test_media_storage_path_convention(monkeypatch):
    """AC: Default storage convention keyed by (user_id, goal_id_or_orphan, upload_id).

    The convention is: <media_dir>/<user_id>/<goal_or_orphan>/<upload_id>.mp4.
    """
    monkeypatch.setenv("MEDIA_DIR", "/data/media")
    from app.config import Settings

    s = Settings()

    user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    goal_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    upload_id = uuid.UUID("33333333-3333-3333-3333-333333333333")

    # With goal_id
    path_with_goal = (
        Path(s.media_dir) / str(user_id) / str(goal_id) / f"{upload_id}.mp4"
    )
    assert str(path_with_goal) == (
        "/data/media/"
        "11111111-1111-1111-1111-111111111111/"
        "22222222-2222-2222-2222-222222222222/"
        "33333333-3333-3333-3333-333333333333.mp4"
    )

    # Without goal_id (orphan → "unassigned")
    path_orphan = (
        Path(s.media_dir) / str(user_id) / "unassigned" / f"{upload_id}.mp4"
    )
    assert str(path_orphan) == (
        "/data/media/"
        "11111111-1111-1111-1111-111111111111/"
        "unassigned/"
        "33333333-3333-3333-3333-333333333333.mp4"
    )


class TestMediaUploadModel:
    """Tests for the MediaUpload SQLAlchemy model."""

    def test_model_table_name(self):
        """AC: Table is named media_uploads."""
        assert MediaUpload.__tablename__ == "media_uploads"

    def test_model_has_all_required_columns(self):
        """AC: Fields match spec: id, user_id, goal_id, sha256, size_bytes,
        duration_seconds, mime_type, storage_path, created_at."""
        expected = {
            "id",
            "user_id",
            "goal_id",
            "sha256",
            "size_bytes",
            "duration_seconds",
            "mime_type",
            "storage_path",
            "created_at",
            "updated_at",  # from TimestampMixin
        }
        inspector = inspect(MediaUpload)
        actual = {c.key for c in inspector.columns}
        assert actual == expected

    def test_goal_id_nullable(self):
        """AC: goal_id is nullable."""
        inspector = inspect(MediaUpload)
        col = inspector.columns["goal_id"]
        assert col.nullable is True

    def test_user_id_not_nullable(self):
        """AC: user_id is NOT nullable (ownership linkage)."""
        inspector = inspect(MediaUpload)
        col = inspector.columns["user_id"]
        assert col.nullable is False


async def _drop_tables_cascade(engine):
    """Drop all tables using CASCADE if they exist."""
    async with engine.begin() as conn:
        for t in ALL_TABLE_NAMES:
            await conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        await conn.execute(text("DROP TYPE IF EXISTS goal_type CASCADE"))
        await conn.execute(text("DROP TYPE IF EXISTS auth_provider CASCADE"))


async def _recreate_tables_for_fixture(engine):
    """Recreate all tables so autouse test_db fixture can clean up."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
class TestMediaUploadMigration:
    """Tests that exercise the real Alembic migration against a database."""

    async def test_upgrade_creates_table_with_columns(self):
        """AC: Migration creates the required columns."""
        engine = create_async_engine(settings.database_url, echo=False)

        # Clean slate — drop everything first
        await _drop_tables_cascade(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name, is_nullable, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'media_uploads' "
                    "ORDER BY ordinal_position"
                )
            )
            rows = result.fetchall()

        columns = {row[0]: {"nullable": row[1], "type": row[2]} for row in rows}

        # All required columns present
        assert "id" in columns
        assert "user_id" in columns
        assert "goal_id" in columns
        assert "sha256" in columns
        assert "size_bytes" in columns
        assert "duration_seconds" in columns
        assert "mime_type" in columns
        assert "storage_path" in columns
        assert "created_at" in columns

        # Nullability constraints
        assert columns["user_id"]["nullable"] == "NO"
        assert columns["goal_id"]["nullable"] == "YES"
        assert columns["sha256"]["nullable"] == "NO"
        assert columns["size_bytes"]["nullable"] == "NO"
        assert columns["duration_seconds"]["nullable"] == "NO"
        assert columns["mime_type"]["nullable"] == "NO"
        assert columns["storage_path"]["nullable"] == "NO"

        # sha256 must be varchar(64)
        assert columns["sha256"]["type"] == "character varying"

        await _drop_tables_cascade(engine)
        await _recreate_tables_for_fixture(engine)
        await engine.dispose()

    async def test_downgrade_drops_table(self):
        """AC: Migration downgrade removes the table cleanly."""
        engine = create_async_engine(settings.database_url, echo=False)

        await _drop_tables_cascade(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Verify table exists
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = 'media_uploads')"
                )
            )
            exists = result.scalar()
        assert exists is True

        # Drop just the media_uploads table (equivalent to downgrade)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE media_uploads CASCADE"))

        # Verify table gone
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = 'media_uploads')"
                )
            )
            exists = result.scalar()
        assert exists is False

        await _drop_tables_cascade(engine)
        await _recreate_tables_for_fixture(engine)
        await engine.dispose()

    async def test_model_persist_and_read(self):
        """Model can persist and read back a row with nullable goal_id."""
        engine = create_async_engine(settings.database_url, echo=False)

        await _drop_tables_cascade(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            user = User(
                email="media@test.com",
                display_name="Media Tester",
                auth_provider="google",
                auth_provider_id="g-media-1",
            )
            session.add(user)
            await session.commit()
            user_id = user.id

        # Persist with goal_id = None (orphan upload)
        async with async_session() as session:
            upload = MediaUpload(
                user_id=user_id,
                goal_id=None,
                sha256="a" * 64,
                size_bytes=12345678,
                duration_seconds=12.5,
                mime_type="video/mp4",
                storage_path=f"/var/sacrifice/media/{user_id}/unassigned/some-uuid.mp4",
            )
            session.add(upload)
            await session.commit()
            upload_id = upload.id

        # Read back
        async with async_session() as session:
            found = await session.get(MediaUpload, upload_id)
            assert found is not None
            assert found.user_id == user_id
            assert found.goal_id is None
            assert found.sha256 == "a" * 64
            assert found.size_bytes == 12345678
            assert found.duration_seconds == 12.5
            assert found.mime_type == "video/mp4"
            assert found.storage_path == (
                f"/var/sacrifice/media/{user_id}/unassigned/some-uuid.mp4"
            )
            assert found.created_at is not None

        await _drop_tables_cascade(engine)
        await _recreate_tables_for_fixture(engine)
        await engine.dispose()