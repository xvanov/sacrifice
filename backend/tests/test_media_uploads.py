import os
import uuid

from sqlalchemy import text
from sqlalchemy.orm import clear_mappers

from app.config import Settings


# ── AC2: Configurable media storage root ────────────────────────────

def test_media_dir_default():
    """media_dir defaults to /var/sacrifice/media when SACRIFICE_MEDIA_DIR is unset."""
    os.environ.pop("SACRIFICE_MEDIA_DIR", None)
    # Also pop MEDIA_DIR so the alias-only path is exercised
    os.environ.pop("MEDIA_DIR", None)
    s = Settings()
    assert s.media_dir == "/var/sacrifice/media"


def test_media_dir_env_override():
    """media_dir respects the SACRIFICE_MEDIA_DIR env variable."""
    os.environ["SACRIFICE_MEDIA_DIR"] = "/custom/videos"
    try:
        s = Settings()
        assert s.media_dir == "/custom/videos"
    finally:
        del os.environ["SACRIFICE_MEDIA_DIR"]


# ── AC1: media_uploads persistence model ────────────────────────────

def test_media_upload_table_exists():
    """The media_uploads table is present in Base.metadata."""
    from app.models.media_upload import MediaUpload
    assert MediaUpload.__tablename__ == "media_uploads"
    assert MediaUpload.__table__.name == "media_uploads"


def test_media_upload_columns_match_ac1():
    """media_uploads has exactly the columns declared in AC1."""
    from app.models.media_upload import MediaUpload

    col_names = {c.name for c in MediaUpload.__table__.columns}
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
    }
    assert col_names == expected


def test_media_upload_goal_id_nullable():
    """goal_id is nullable so orphan uploads are supported."""
    from app.models.media_upload import MediaUpload

    col = MediaUpload.__table__.c.goal_id
    assert col.nullable is True


def test_media_upload_user_id_not_nullable():
    """Every upload must be owned by a user."""
    from app.models.media_upload import MediaUpload

    col = MediaUpload.__table__.c.user_id
    assert col.nullable is False


def test_media_upload_sha256_is_varchar_64():
    from app.models.media_upload import MediaUpload

    col = MediaUpload.__table__.c.sha256
    assert col.type.length == 64


def test_media_upload_mime_type_is_varchar_127():
    from app.models.media_upload import MediaUpload

    col = MediaUpload.__table__.c.mime_type
    assert col.type.length == 127


def test_media_upload_storage_path_is_varchar_1024():
    from app.models.media_upload import MediaUpload

    col = MediaUpload.__table__.c.storage_path
    assert col.type.length == 1024


def test_media_upload_created_at_has_server_default():
    from app.models.media_upload import MediaUpload

    col = MediaUpload.__table__.c.created_at
    assert col.server_default is not None


# ── Integration: create_all round-trips ─────────────────────────────

async def test_media_upload_create_all_round_trip(test_db):
    """Base.metadata.create_all creates the table, and we can insert a row."""
    from app.models.media_upload import MediaUpload
    from app.models.user import User
    from app.database import get_db

    # Grab a session from the test fixture
    db_gen = get_db()
    session = await anext(db_gen)

    try:
        # Verify the table was created
        result = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'media_uploads' ORDER BY ordinal_position"
            )
        )
        cols = [row[0] for row in result.fetchall()]
        assert "id" in cols
        assert "user_id" in cols
        assert "goal_id" in cols
        assert "sha256" in cols
        assert "size_bytes" in cols
        assert "duration_seconds" in cols
        assert "mime_type" in cols
        assert "storage_path" in cols
        assert "created_at" in cols

        # Create a user so we can satisfy the FK
        user_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name, "
                "auth_provider, auth_provider_id) "
                "VALUES (:id, :email, :pw, :name, :provider, :provider_id)"
            ),
            {
                "id": user_id,
                "email": "test@example.com",
                "pw": "hashed",
                "name": "Test User",
                "provider": "email",
                "provider_id": "test-user-1",
            },
        )

        upload_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO media_uploads "
                "(id, user_id, goal_id, sha256, size_bytes, duration_seconds, "
                "mime_type, storage_path) "
                "VALUES (:id, :user_id, NULL, :sha256, :size_bytes, "
                ":duration_seconds, :mime_type, :storage_path)"
            ),
            {
                "id": upload_id,
                "user_id": user_id,
                "sha256": "a" * 64,
                "size_bytes": 1024,
                "duration_seconds": 12.5,
                "mime_type": "video/mp4",
                "storage_path": "/var/sacrifice/media/user_id/orphan/uuid.mp4",
            },
        )
        await session.commit()

        row = await session.execute(
            text("SELECT * FROM media_uploads WHERE id = :id"), {"id": upload_id}
        )
        r = row.fetchone()
        assert r is not None
        assert r.goal_id is None
        assert r.sha256 == "a" * 64
        assert r.size_bytes == 1024
        assert r.duration_seconds == 12.5
        assert r.mime_type == "video/mp4"
    finally:
        await session.rollback()
        await session.close()