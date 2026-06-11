"""Tests for D008 media_uploads model, config, and migration."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic.command import downgrade as alembic_downgrade
from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig

from app.config import Settings
from app.models.goal import Goal
from app.models.media import MediaUpload
from app.models.user import User
from app.services.uploads import media_storage_path

# Every table that Base.metadata knows about (must stay in sync with models).
ALL_TABLE_NAMES = [
    "media_uploads",
    "proof_submissions",
    "payments",
    "notifications",
    "goal_criteria",
    "goals",
    "users",
]

# All custom ENUM types created by the initial migration + later migrations.
ALL_ENUM_TYPES = [
    "goal_type",
    "recurrence",
    "goal_status",
    "criteria_type",
    "notification_type",
    "payment_status",
    "verification_status",
]


# ── config tests ────────────────────────────────────────────────────────────


def test_media_dir_config_default(monkeypatch):
    """AC: Default storage root is /var/sacrifice/media via SACRIFICE_MEDIA_DIR."""
    monkeypatch.delenv("SACRIFICE_MEDIA_DIR", raising=False)
    s = Settings()
    assert s.sacrifice_media_dir == "/var/sacrifice/media"


def test_media_dir_config_can_be_overridden(monkeypatch):
    """AC: SACRIFICE_MEDIA_DIR env var overrides the default storage root."""
    monkeypatch.setenv("SACRIFICE_MEDIA_DIR", "/custom/path")
    s = Settings()
    assert s.sacrifice_media_dir == "/custom/path"


def test_media_storage_path_convention():
    """AC: media_storage_path produces canonical goal and orphan paths."""
    user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    goal_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    upload_id = uuid.UUID("33333333-3333-3333-3333-333333333333")

    # With goal_id — uses the real production helper
    path_with_goal = media_storage_path(user_id, goal_id, upload_id,
                                        media_dir="/data/media")
    assert path_with_goal == (
        Path("/data/media") / str(user_id) / str(goal_id) / f"{upload_id}.mp4"
    )

    # Without goal_id → "unassigned" segment
    path_orphan = media_storage_path(user_id, None, upload_id,
                                     media_dir="/data/media")
    assert path_orphan == (
        Path("/data/media") / str(user_id) / "unassigned" / f"{upload_id}.mp4"
    )


# ── model persistence tests ─────────────────────────────────────────────────


# ── migration test helpers ──────────────────────────────────────────────────

_REVISION = "13ac1742b6ea"
_DOWN_REVISION = "9d4f2a6e1c70"


def _make_alembic_config(db_url: str) -> AlembicConfig:
    """Return an Alembic Config pointed at the test database."""
    root = os.path.join(os.path.dirname(__file__), "..", "alembic")
    cfg_path = os.path.join(root, "..", "alembic.ini")
    cfg = AlembicConfig(cfg_path)
    cfg.set_main_option("script_location", os.path.join(root, "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("+asyncpg", "+psycopg2"))
    # Prevent Alembic from printing banners during tests.
    cfg.print_stdout = lambda *a, **kw: None
    return cfg


async def _alembic_upgrade_to(engine, cfg: AlembicConfig, revision: str) -> None:
    """Run Alembic upgrade to *revision* in a blocking thread executor."""
    import asyncio

    await asyncio.to_thread(alembic_upgrade, cfg, revision)


async def _alembic_downgrade_to(engine, cfg: AlembicConfig, revision: str) -> None:
    """Run Alembic downgrade to *revision* in a blocking thread executor."""
    import asyncio

    await asyncio.to_thread(alembic_downgrade, cfg, revision)


async def _drop_everything(engine) -> None:
    """Drop every user table and custom type so migrations start clean."""
    async with engine.begin() as conn:
        for t in ALL_TABLE_NAMES:
            await conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        for typ in ALL_ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {typ} CASCADE"))
        # Also drop alembic_version so the next migration can re-stamp.
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))


async def _recreate_all_tables(engine) -> None:
    """Recreate all tables from metadata so the conftest fixture teardown works."""
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _assert_table_exists(engine, table_name: str) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :name)"
            ),
            {"name": table_name},
        )
        exists = result.scalar()
    assert exists is True, f"Table {table_name} should exist"


async def _assert_table_missing(engine, table_name: str) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :name)"
            ),
            {"name": table_name},
        )
        exists = result.scalar()
    assert exists is False, f"Table {table_name} should not exist"


# ── migration tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMediaUploadMigration:
    """Tests that exercise the real Alembic migration against a database."""

    async def test_upgrade_creates_table_with_columns(self):
        """AC: Alembic upgrade to 13ac1742b6ea creates media_uploads with all required columns."""
        from app.config import settings as app_settings

        engine = create_async_engine(app_settings.database_url, echo=False)
        try:
            await _drop_everything(engine)

            cfg = _make_alembic_config(app_settings.database_url)

            # Start from the revision just before ours.
            await _alembic_upgrade_to(engine, cfg, _DOWN_REVISION)

            # Now run our target migration.
            await _alembic_upgrade_to(engine, cfg, _REVISION)

            # Verify the table now exists with the expected columns.
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

            assert "id" in columns
            assert "user_id" in columns
            assert "goal_id" in columns
            assert "sha256" in columns
            assert "size_bytes" in columns
            assert "duration_seconds" in columns
            assert "mime_type" in columns
            assert "storage_path" in columns
            assert "created_at" in columns
            assert "updated_at" not in columns  # story schema does NOT include updated_at

            # Nullability constraints
            assert columns["user_id"]["nullable"] == "NO"
            assert columns["goal_id"]["nullable"] == "YES"
            assert columns["sha256"]["nullable"] == "NO"
            assert columns["size_bytes"]["nullable"] == "NO"
            assert columns["duration_seconds"]["nullable"] == "NO"
            assert columns["mime_type"]["nullable"] == "NO"
            assert columns["storage_path"]["nullable"] == "NO"
        finally:
            await _drop_everything(engine)
            await _recreate_all_tables(engine)
            await engine.dispose()

    async def test_downgrade_drops_table(self):
        """AC: Alembic downgrade from 13ac1742b6ea to 9d4f2a6e1c70 drops media_uploads."""
        from app.config import settings as app_settings

        engine = create_async_engine(app_settings.database_url, echo=False)
        try:
            await _drop_everything(engine)

            cfg = _make_alembic_config(app_settings.database_url)

            # Upgrade to our target revision.
            await _alembic_upgrade_to(engine, cfg, _REVISION)
            await _assert_table_exists(engine, "media_uploads")

            # Downgrade one step.
            await _alembic_downgrade_to(engine, cfg, _DOWN_REVISION)
            await _assert_table_missing(engine, "media_uploads")
        finally:
            await _drop_everything(engine)
            await _recreate_all_tables(engine)
            await engine.dispose()

    @pytest.mark.parametrize(
        "goal_id_kind, sha256, size_bytes, duration",
        [
            ("orphan", "a" * 64, 12345678, 12.5),
            ("linked", "b" * 64, 9876543, 30.0),
        ],
    )
    async def test_model_persist_and_read(
        self, goal_id_kind, sha256, size_bytes, duration
    ):
        """AC: MediaUpload persists and round-trips whether orphan or goal-linked."""
        from app.config import settings as app_settings

        engine = create_async_engine(app_settings.database_url, echo=False)
        try:
            await _drop_everything(engine)

            cfg = _make_alembic_config(app_settings.database_url)
            await _alembic_upgrade_to(engine, cfg, "head")

            async_session = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )

            async with async_session() as session:
                user = User(
                    email="persist@test.com",
                    display_name="Persist Tester",
                    auth_provider="google",
                    auth_provider_id=f"g-persist-{goal_id_kind}",
                )
                session.add(user)
                await session.commit()
                user_id = user.id

                goal_id = None
                if goal_id_kind == "linked":
                    goal = Goal(
                        user_id=user.id,
                        title="Test Goal for Upload",
                        goal_type="youtube_video",
                        pledge_amount=5000,
                        deadline=datetime(2027, 1, 1, tzinfo=timezone.utc),
                    )
                    session.add(goal)
                    await session.commit()
                    goal_id = goal.id

                expected_storage_path = str(
                    media_storage_path(user_id, goal_id, uuid.UUID("00000000-0000-0000-0000-000000000000"))
                )

            async with async_session() as session:
                upload = MediaUpload(
                    user_id=user_id,
                    goal_id=goal_id,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    duration_seconds=duration,
                    mime_type="video/mp4",
                    storage_path=expected_storage_path,
                )
                session.add(upload)
                await session.commit()
                upload_id = upload.id

            async with async_session() as session:
                found = await session.get(MediaUpload, upload_id)
                assert found is not None
                assert found.user_id == user_id
                assert found.goal_id == goal_id
                assert found.sha256 == sha256
                assert found.size_bytes == size_bytes
                assert found.duration_seconds == duration
                assert found.mime_type == "video/mp4"
                assert found.storage_path == expected_storage_path
                assert found.created_at is not None
        finally:
            await _drop_everything(engine)
            await _recreate_all_tables(engine)
            await engine.dispose()

    async def test_user_id_not_null_db_constraint(self):
        """AC: user_id is NOT NULL — database rejects insert without it."""
        from app.config import settings as app_settings

        engine = create_async_engine(app_settings.database_url, echo=False)
        try:
            await _drop_everything(engine)

            cfg = _make_alembic_config(app_settings.database_url)
            await _alembic_upgrade_to(engine, cfg, "head")

            async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            with pytest.raises(IntegrityError) as exc_info:
                async with async_session() as session:
                    # Bypass the ORM by using raw SQL so the DB constraint is exercised
                    await session.execute(
                        text(
                            "INSERT INTO media_uploads (id, user_id, goal_id, sha256, "
                            "size_bytes, duration_seconds, mime_type, storage_path) "
                            "VALUES (gen_random_uuid(), NULL, NULL, :sha256, :size_bytes, "
                            ":duration_seconds, :mime_type, :storage_path)"
                        ),
                        {
                            "sha256": "a" * 64,
                            "size_bytes": 12345678,
                            "duration_seconds": 12.5,
                            "mime_type": "video/mp4",
                            "storage_path": "/tmp/test.mp4",
                        },
                    )
                    await session.commit()
            # Verify the underlying driver error is a NOT NULL violation on user_id.
            # SQLAlchemy's asyncpg dialect wraps the raw asyncpg exception; the
            # IntegrityError message embeds the asyncpg exception class name and
            # the column reference.
            err_msg = str(exc_info.value.orig)
            assert "NotNullViolationError" in err_msg
            assert "user_id" in err_msg.lower()
        finally:
            await _drop_everything(engine)
            await _recreate_all_tables(engine)
            await engine.dispose()