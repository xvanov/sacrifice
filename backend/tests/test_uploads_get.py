import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.database import get_db
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client,
    email="test@example.com",
    name="Test User",
    sub="test-sub-123",
    token="valid-token",
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": name,
            "sub": sub,
            "picture": None,
            "email_verified": True,
        }
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _seed_upload(*, user_id: str, goal_id: str | None = None, session=None):
    """Insert a media_uploads row via the overridden test DB session and return the upload id."""
    close_after = session is None
    if session is None:
        override = app.dependency_overrides[get_db]
        session = await anext(override())
    try:
        upload_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO media_uploads "
                "(id, user_id, goal_id, sha256, size_bytes, duration_seconds, "
                "mime_type, storage_path) "
                "VALUES (:id, :user_id, :goal_id, :sha256, :size_bytes, "
                ":duration_seconds, :mime_type, :storage_path)"
            ),
            {
                "id": upload_id,
                "user_id": uuid.UUID(user_id),
                "goal_id": uuid.UUID(goal_id) if goal_id else None,
                "sha256": "a" * 64,
                "size_bytes": 1024,
                "duration_seconds": 12.5,
                "mime_type": "video/mp4",
                "storage_path": "/var/sacrifice/media/user_id/orphan/uuid.mp4",
            },
        )
        await session.commit()
        return upload_id
    finally:
        if close_after:
            await session.close()


# ── GET /api/uploads/{upload_id} ─────────────────────────────────────


async def test_get_upload_returns_200_with_exact_contract_for_owner():
    """Owner gets 200 with upload_id, goal_id (null), sha256, size_bytes,
    duration_seconds, mime_type, and created_at ending in Z."""
    async with make_client() as client:
        token, user = await _auth(client)
        upload_id = await _seed_upload(user_id=user["id"])

        resp = await client.get(
            f"/api/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["upload_id"] == str(upload_id)
    assert body["goal_id"] is None
    assert body["sha256"] == "a" * 64
    assert body["size_bytes"] == 1024
    assert body["duration_seconds"] == 12.5
    assert body["mime_type"] == "video/mp4"
    assert "created_at" in body
    # api_spec.md requires Z-suffix UTC, not +00:00
    created_at = body["created_at"]
    assert created_at.endswith("Z"), f"created_at must end in Z: {created_at}"
    assert created_at.count("T") == 1, f"created_at must be ISO 8601: {created_at}"


async def test_get_upload_returns_200_with_goal_id_when_present():
    """Owner gets 200 with a non-null goal_id when upload is associated with a goal."""
    async with make_client() as client:
        token, user = await _auth(client)

        goal_id = uuid.uuid4()

        override = app.dependency_overrides[get_db]
        session = await anext(override())
        try:
            await session.execute(
                text(
                    "INSERT INTO goals (id, user_id, title, description, deadline, "
                    "pledge_amount, goal_type, status, currency, timezone) "
                    "VALUES (:id, :user_id, :title, :desc, :deadline, :pledge, "
                    ":goal_type, :status, :currency, :timezone)"
                ),
                {
                    "id": goal_id,
                    "user_id": uuid.UUID(user["id"]),
                    "title": "Test Goal",
                    "desc": "desc",
                    "deadline": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "pledge": 5000,
                    "goal_type": "youtube_video",
                    "status": "draft",
                    "currency": "usd",
                    "timezone": "UTC",
                },
            )
            await session.commit()

            upload_id = await _seed_upload(
                user_id=user["id"],
                goal_id=str(goal_id),
                session=session,
            )
        finally:
            await session.close()

        resp = await client.get(
            f"/api/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["upload_id"] == str(upload_id)
    assert body["goal_id"] == str(goal_id)


async def test_get_upload_returns_403_for_non_owner():
    """User B gets 403 when requesting an upload owned by User A."""
    async with make_client() as client:
        token_a, user_a = await _auth(client)
        upload_id = await _seed_upload(user_id=user_a["id"])

        token_b, _ = await _auth(
            client,
            email="other@test.com",
            name="Other",
            sub="other-sub",
            token="other-token",
        )

        resp = await client.get(
            f"/api/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )

    assert resp.status_code == 403


async def test_get_upload_returns_404_for_unknown_id():
    """404 when the upload_id is a valid UUID that does not exist."""
    async with make_client() as client:
        token, _ = await _auth(client)

        unknown_id = uuid.uuid4()
        resp = await client.get(
            f"/api/uploads/{unknown_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 404


async def test_get_upload_returns_401_unauthenticated():
    """401 when no Authorization header is provided."""
    upload_id = uuid.uuid4()

    async with make_client() as client:
        resp = await client.get(f"/api/uploads/{upload_id}")

    assert resp.status_code == 401
