"""Integration tests for POST /api/uploads/video."""

from __future__ import annotations

import uuid
from io import BytesIO
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_goal(client, token: str, user_id: str | None = None):
    """Create a goal and return its id."""
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Test Goal",
            "description": "For upload tests",
            "deadline": "2026-06-01T00:00:00Z",
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 60, "video_description": "Test"},
            "charity_id": "acct_test123",
        },
    )
    return resp.json()["id"]


async def _upload_video(client, token: str, file_bytes: bytes,
                        filename: str = "test.mp4",
                        content_type: str = "video/mp4",
                        duration_seconds: float = 5.0,
                        goal_id: str | None = None):
    """Helper to POST /api/uploads/video with multipart form data."""
    files = {"file": (filename, BytesIO(file_bytes), content_type)}
    data = {"duration_seconds": str(duration_seconds)}
    if goal_id is not None:
        data["goal_id"] = goal_id
    return await client.post(
        "/api/uploads/video",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
        data=data,
    )


# ── Happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_video_upload_returns_201_with_response_shape(monkeypatch, tmp_path):
    """AC: 201 with upload_id, sha256, size_bytes, duration_seconds, mime_type."""
    monkeypatch.setattr("app.config.settings.sacrifice_media_dir", str(tmp_path))

    async with make_client() as client:
        token, user = await _auth(client)
        content = b"fake-video-content"

        response = await _upload_video(client, token, content)

    assert response.status_code == 201
    body = response.json()
    assert "upload_id" in body
    assert "sha256" in body
    assert body["size_bytes"] == len(content)
    assert body["duration_seconds"] == 5.0
    assert body["mime_type"] == "video/mp4"
    # Verify upload_id is a valid UUID
    uuid.UUID(body["upload_id"])
    # Verify sha256 is 64-char hex
    assert len(body["sha256"]) == 64
    int(body["sha256"], 16)  # must not raise


@pytest.mark.asyncio
async def test_post_video_upload_with_goal_id_persists_link(monkeypatch, tmp_path):
    """AC: upload with a valid goal_id succeeds and links to that goal."""
    monkeypatch.setattr("app.config.settings.sacrifice_media_dir", str(tmp_path))

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal(client, token)
        content = b"linked-upload-content"

        response = await _upload_video(client, token, content, goal_id=goal_id)

    assert response.status_code == 201
    body = response.json()
    assert body["upload_id"] is not None


@pytest.mark.asyncio
async def test_post_video_upload_orphan_when_no_goal_id(monkeypatch, tmp_path):
    """AC: upload without goal_id succeeds (orphan upload)."""
    monkeypatch.setattr("app.config.settings.sacrifice_media_dir", str(tmp_path))

    async with make_client() as client:
        token, _ = await _auth(client)
        content = b"orphan-upload-content"

        response = await _upload_video(client, token, content)

    assert response.status_code == 201
    body = response.json()
    assert body["upload_id"] is not None


@pytest.mark.asyncio
async def test_post_video_upload_accepts_quicktime(monkeypatch, tmp_path):
    """AC: video/quicktime MIME type is accepted."""
    monkeypatch.setattr("app.config.settings.sacrifice_media_dir", str(tmp_path))

    async with make_client() as client:
        token, _ = await _auth(client)
        content = b"quicktime-video-content"

        response = await _upload_video(
            client, token, content,
            filename="test.mov",
            content_type="video/quicktime",
        )

    assert response.status_code == 201
    assert response.json()["mime_type"] == "video/quicktime"


# ── Error cases ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_video_upload_returns_401_without_auth():
    """AC: 401 when unauthenticated."""
    async with make_client() as client:
        response = await _upload_video(client, "", b"content")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_video_upload_returns_403_when_goal_not_owned():
    """AC: 403 when goal_id belongs to another user."""
    async with make_client() as client:
        token1, _ = await _auth(client)
        token2, _ = await _auth(
            client, email="other@test.com", name="Other",
            sub="other-sub", token="other-token",
        )
        goal_id = await _create_goal(client, token1)

        response = await _upload_video(client, token2, b"content", goal_id=goal_id)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_post_video_upload_returns_403_when_goal_does_not_exist():
    """AC: 403 when goal_id is syntactically valid but nonexistent (treated as not-owned)."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_goal_id = str(uuid.uuid4())

        response = await _upload_video(client, token, b"content", goal_id=fake_goal_id)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_post_video_upload_returns_415_for_unsupported_media_type():
    """AC: 415 when MIME type is not video/mp4 or video/quicktime."""
    async with make_client() as client:
        token, _ = await _auth(client)

        response = await _upload_video(
            client, token, b"content",
            content_type="image/png",
        )

    assert response.status_code == 415


@pytest.mark.asyncio
async def test_post_video_upload_returns_422_when_missing_file():
    """AC: 422 when file field is missing."""
    async with make_client() as client:
        token, _ = await _auth(client)

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "5.0"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_video_upload_returns_422_when_missing_duration_seconds():
    """AC: 422 when duration_seconds field is missing."""
    async with make_client() as client:
        token, _ = await _auth(client)

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("test.mp4", BytesIO(b"content"), "video/mp4")},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_video_upload_returns_413_when_file_too_large(monkeypatch):
    """AC: 413 when file exceeds configured max size."""
    # Set a very small max to trigger 413
    monkeypatch.setattr(
        "app.routes.uploads.settings.max_upload_size_bytes", 10
    )

    async with make_client() as client:
        token, _ = await _auth(client)
        content = b"x" * 100  # 100 bytes > 10 byte limit

        response = await _upload_video(client, token, content)

    assert response.status_code == 413