"""Tests for D008 media_uploads model, config, and migration."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic.command import downgrade as alembic_downgrade
from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from app.config import Settings
from app.models.goal import Goal
from app.models.media import MediaUpload, media_storage_path
from app.models.user import User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Every table that Base.metadata knows about (must stay in sync with models).
ALL_TABLE_NAMES = [
    "audit_events",
    "chat_spend_ledger",
    "chat_sessions",
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
    "audit_event_type",
    "goal_type",
    "recurrence",
    "goal_status",
    "criteria_type",
    "notification_type",
    "payment_status",
    "verification_status",
    "chat_session_status",
]


# ── config tests ────────────────────────────────────────────────────────────


def test_media_dir_default_when_env_unset(monkeypatch):
    """AC: default media root is /var/sacrifice/media when SACRIFICE_MEDIA_DIR
    is unset (${SACRIFICE_MEDIA_DIR:-/var/sacrifice/media})."""
    monkeypatch.delenv("SACRIFICE_MEDIA_DIR", raising=False)
    s = Settings()
    assert s.sacrifice_media_dir == "/var/sacrifice/media"


def test_media_dir_config_honors_env_override(monkeypatch):
    """AC: SACRIFICE_MEDIA_DIR env override is honored by Settings().

    Verifies that the Settings class reads the SACRIFICE_MEDIA_DIR
    environment variable — not a constructor kwarg — and surfaces it
    as sacrifice_media_dir.
    """
    monkeypatch.setenv("SACRIFICE_MEDIA_DIR", "/env/override/path")
    s = Settings()
    assert s.sacrifice_media_dir == "/env/override/path"


# ── storage-path convention tests ───────────────────────────────────────────


def test_media_storage_path_convention(monkeypatch):
    """AC: media_storage_path produces <root>/<user>/orphan/<upload>.mp4.

    One direct helper test with a single exact-path assertion for the
    orphan (no-goal) case, per the story contract.
    """
    import app.config as _app_cfg

    user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    upload_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    monkeypatch.setenv("SACRIFICE_MEDIA_DIR", "/var/sacrifice/media")
    # Re-create settings so it picks up the patched env.
    _app_cfg.settings = _app_cfg.Settings()
    from app.models.media import media_storage_path as _msp

    path = _msp(user_id, None, upload_id)

    assert path == (
        "/var/sacrifice/media"
        "/11111111-1111-1111-1111-111111111111"
        "/orphan"
        "/22222222-2222-2222-2222-222222222222.mp4"
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
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :name)"),
            {"name": table_name},
        )
        exists = result.scalar()
    assert exists is True, f"Table {table_name} should exist"


async def _assert_table_missing(engine, table_name: str) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :name)"),
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

            # Foreign-key ownership linkage
            async with engine.connect() as conn:
                fk_result = await conn.execute(
                    text(
                        "SELECT kcu.column_name, ccu.table_name AS foreign_table_name, "
                        "ccu.column_name AS foreign_column_name "
                        "FROM information_schema.table_constraints AS tc "
                        "JOIN information_schema.key_column_usage AS kcu "
                        "  ON tc.constraint_name = kcu.constraint_name "
                        "JOIN information_schema.constraint_column_usage AS ccu "
                        "  ON ccu.constraint_name = tc.constraint_name "
                        "WHERE tc.constraint_type = 'FOREIGN KEY' "
                        "  AND tc.table_name = 'media_uploads'"
                    )
                )
                fks = {(row[0], row[1], row[2]) for row in fk_result.fetchall()}

            assert ("user_id", "users", "id") in fks, (
                "media_uploads.user_id must reference users.id"
            )
            assert ("goal_id", "goals", "id") in fks, (
                "media_uploads.goal_id must reference goals.id"
            )

            # Server default on created_at
            async with engine.connect() as conn:
                col_result = await conn.execute(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_name = 'media_uploads' AND column_name = 'created_at'"
                    )
                )
                default = col_result.scalar()
            assert default is not None, "created_at must have a server default"
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

    async def test_model_persist_orphan(self):
        """AC: MediaUpload with goal_id=NULL persists and round-trips all fields."""
        from app.config import settings as app_settings

        engine = create_async_engine(app_settings.database_url, echo=False)
        try:
            await _drop_everything(engine)

            cfg = _make_alembic_config(app_settings.database_url)
            await _alembic_upgrade_to(engine, cfg, "head")

            async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            user_id: uuid.UUID
            async with async_session() as session:
                user = User(
                    email="persist-orphan@test.com",
                    display_name="Orphan Uploader",
                    auth_provider="google",
                    auth_provider_id="g-persist-orphan",
                )
                session.add(user)
                await session.commit()
                user_id = user.id

            upload_id: uuid.UUID
            expected_path: str
            async with async_session() as session:
                upload = MediaUpload(
                    user_id=user_id,
                    goal_id=None,
                    sha256="a" * 64,
                    size_bytes=12345678,
                    duration_seconds=12.5,
                    mime_type="video/mp4",
                    storage_path="",  # filled after flush gives us the id
                )
                session.add(upload)
                await session.flush()
                upload.storage_path = media_storage_path(user_id, None, upload.id)
                await session.commit()
                upload_id = upload.id
                expected_path = media_storage_path(user_id, None, upload.id)

            async with async_session() as session:
                found = await session.get(MediaUpload, upload_id)
                assert found is not None
                assert found.user_id == user_id
                assert found.goal_id is None
                assert found.sha256 == "a" * 64
                assert found.size_bytes == 12345678
                assert found.duration_seconds == 12.5
                assert found.mime_type == "video/mp4"
                assert found.created_at is not None
                assert found.storage_path == expected_path
        finally:
            await _drop_everything(engine)
            await _recreate_all_tables(engine)
            await engine.dispose()

    async def test_model_persist_goal_linked(self):
        """AC: MediaUpload with goal_id linked to an owned goal persists and round-trips all fields."""
        from app.config import settings as app_settings

        engine = create_async_engine(app_settings.database_url, echo=False)
        try:
            await _drop_everything(engine)

            cfg = _make_alembic_config(app_settings.database_url)
            await _alembic_upgrade_to(engine, cfg, "head")

            async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            user_id: uuid.UUID
            goal_id: uuid.UUID
            async with async_session() as session:
                user = User(
                    email="persist-linked@test.com",
                    display_name="Linked Uploader",
                    auth_provider="google",
                    auth_provider_id="g-persist-linked",
                )
                session.add(user)
                await session.commit()
                user_id = user.id

                goal = Goal(
                    user_id=user.id,
                    title="Test Goal for Linked Upload",
                    goal_type="youtube_video",
                    pledge_amount=5000,
                    deadline=datetime(2027, 1, 1, tzinfo=UTC),
                )
                session.add(goal)
                await session.commit()
                goal_id = goal.id

            upload_id: uuid.UUID
            expected_path: str
            async with async_session() as session:
                upload = MediaUpload(
                    user_id=user_id,
                    goal_id=goal_id,
                    sha256="b" * 64,
                    size_bytes=9876543,
                    duration_seconds=30.0,
                    mime_type="video/mp4",
                    storage_path="",  # filled after flush gives us the id
                )
                session.add(upload)
                await session.flush()
                upload.storage_path = media_storage_path(user_id, goal_id, upload.id)
                await session.commit()
                upload_id = upload.id
                expected_path = media_storage_path(user_id, goal_id, upload.id)

            async with async_session() as session:
                found = await session.get(MediaUpload, upload_id)
                assert found is not None
                assert found.user_id == user_id
                assert found.goal_id == goal_id
                assert found.sha256 == "b" * 64
                assert found.size_bytes == 9876543
                assert found.duration_seconds == 30.0
                assert found.mime_type == "video/mp4"
                assert found.created_at is not None
                assert found.storage_path == expected_path
        finally:
            await _drop_everything(engine)
            await _recreate_all_tables(engine)
            await engine.dispose()
