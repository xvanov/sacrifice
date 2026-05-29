"""
Tests for the media upload endpoints.

POST /api/uploads/video — multipart video upload
GET  /api/uploads/{upload_id} — retrieve upload metadata

These tests MUST go RED pre-implementation — the routes and services do not exist yet.
"""

import hashlib
import io
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# POST /api/uploads/video
# ---------------------------------------------------------------------------


async def test_post_video_upload_returns_201_with_expected_response_shape():
    """Upload a small mp4 fixture and assert the 201 response shape, then verify
    the upload is retrievable by the owner."""
    async with make_client() as client:
        token, user = await _auth(client)

        video_content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42" + b"\x00" * 1024
        files = {"file": ("test.mp4", io.BytesIO(video_content), "video/mp4")}
        data = {"duration_seconds": "5.0"}

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data,
        )

    assert response.status_code == 201
    body = response.json()
    assert "upload_id" in body
    assert "sha256" in body
    assert isinstance(body["sha256"], str)
    assert len(body["sha256"]) == 64  # hex-encoded SHA-256
    assert body["size_bytes"] > 0
    assert body["duration_seconds"] == 5.0
    assert body["mime_type"] == "video/mp4"

    # Verify the upload can be retrieved by the owner with persisted metadata
    upload_id = body["upload_id"]

    async with make_client() as client:
        get_response = await client.get(
            f"/api/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert get_response.status_code == 200
    get_body = get_response.json()
    assert get_body["upload_id"] == upload_id
    assert get_body["sha256"] == body["sha256"]
    assert get_body["size_bytes"] == body["size_bytes"]
    assert get_body["duration_seconds"] == 5.0
    assert get_body["mime_type"] == "video/mp4"
    assert "created_at" in get_body
    assert "goal_id" in get_body
    assert get_body["goal_id"] is None


async def test_post_video_upload_with_goal_id_returns_201():
    """Upload associated with a goal owned by the authenticated user, then
    verify the upload persists with the associated goal_id."""
    async with make_client() as client:
        token, user = await _auth(client)

        # Create a goal first
        with patch("app.routes.auth.verify_google_token") as mock:
            mock.return_value = {"email": "test@example.com", "name": "Test", "sub": "test-sub-123", "picture": None}
            goal_resp = await client.post(
                "/api/goals",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "title": "Camera goal",
                    "description": "Test",
                    "deadline": "2026-12-31T00:00:00Z",
                    "pledge_amount": 5000,
                    "goal_type": "youtube_video",
                    "criteria": {"min_duration_seconds": 60, "video_description": "desc"},
                },
            )
        goal_id = goal_resp.json()["id"]

        video_content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42" + b"\x00" * 2048
        files = {"file": ("test.mp4", io.BytesIO(video_content), "video/mp4")}
        data = {"duration_seconds": "12.5", "goal_id": goal_id}

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["duration_seconds"] == 12.5
    assert body["mime_type"] == "video/mp4"
    assert "upload_id" in body
    assert "sha256" in body
    assert "size_bytes" in body
    assert body["size_bytes"] > 0

    # Verify the upload persisted with the correct goal association
    upload_id = body["upload_id"]
    async with make_client() as client:
        get_response = await client.get(
            f"/api/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert get_response.status_code == 200
    get_body = get_response.json()
    assert get_body["upload_id"] == upload_id
    assert get_body["goal_id"] == goal_id
    assert get_body["duration_seconds"] == 12.5
    assert get_body["size_bytes"] == body["size_bytes"]


async def test_post_video_upload_returns_401_when_unauthenticated():
    """Upload without a token must return 401."""
    async with make_client() as client:
        video_content = b"\x00" * 500
        files = {"file": ("test.mp4", io.BytesIO(video_content), "video/mp4")}
        data = {"duration_seconds": "3.0"}

        response = await client.post(
            "/api/uploads/video",
            files=files,
            data=data,
        )

    assert response.status_code == 401


async def test_post_video_upload_returns_422_when_missing_file():
    """Upload without the 'file' field must return 422."""
    async with make_client() as client:
        token, _ = await _auth(client)

        data = {"duration_seconds": "5.0"}
        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data=data,
        )

    assert response.status_code == 422


async def test_post_video_upload_returns_422_when_missing_duration_seconds():
    """Upload without 'duration_seconds' must return 422."""
    async with make_client() as client:
        token, _ = await _auth(client)

        video_content = b"\x00" * 500
        files = {"file": ("test.mp4", io.BytesIO(video_content), "video/mp4")}

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
        )

    assert response.status_code == 422


async def test_post_video_upload_returns_415_for_unsupported_media_type():
    """Upload with non-video content type must return 415."""
    async with make_client() as client:
        token, _ = await _auth(client)

        files = {"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")}
        data = {"duration_seconds": "1.0"}

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data,
        )

    assert response.status_code == 415


async def test_post_video_upload_returns_403_when_goal_id_not_owned_by_user():
    """Upload associated with another user's goal must return 403."""
    async with make_client() as client:
        token1, _ = await _auth(client)
        token2, _ = await _auth(client, email="other@test.com", name="Other",
                                 sub="other-sub", token="other-token")

        # User 1 creates a goal
        with patch("app.routes.auth.verify_google_token") as mock:
            mock.return_value = {"email": "test@example.com", "name": "Test", "sub": "test-sub-123", "picture": None}
            goal_resp = await client.post(
                "/api/goals",
                headers={"Authorization": f"Bearer {token1}"},
                json={
                    "title": "Goal 1",
                    "description": "Test",
                    "deadline": "2026-12-31T00:00:00Z",
                    "pledge_amount": 5000,
                    "goal_type": "youtube_video",
                    "criteria": {"min_duration_seconds": 60, "video_description": "desc"},
                },
            )
        goal_id = goal_resp.json()["id"]

        # User 2 tries to upload with User 1's goal_id
        video_content = b"\x00" * 500
        files = {"file": ("test.mp4", io.BytesIO(video_content), "video/mp4")}
        data = {"duration_seconds": "3.0", "goal_id": goal_id}

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token2}"},
            files=files,
            data=data,
        )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/uploads/{upload_id}
# ---------------------------------------------------------------------------


async def test_get_upload_returns_200_with_expected_shape():
    """Retrieve an uploaded video's metadata."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # Upload first
        video_content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42" + b"\x00" * 1024
        files = {"file": ("vid.mp4", io.BytesIO(video_content), "video/mp4")}
        data = {"duration_seconds": "7.0"}

        upload_resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data,
        )
        upload_id = upload_resp.json()["upload_id"]

        # Retrieve
        response = await client.get(
            f"/api/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_id"] == upload_id
    assert "sha256" in body
    assert body["size_bytes"] == upload_resp.json()["size_bytes"]
    assert body["duration_seconds"] == 7.0
    assert body["mime_type"] == "video/mp4"
    assert "created_at" in body
    assert "goal_id" in body


async def test_get_upload_returns_401_when_unauthenticated():
    """GET upload without a token must return 401."""
    async with make_client() as client:
        response = await client.get("/api/uploads/some-uuid")
    assert response.status_code == 401


async def test_get_upload_returns_404_for_nonexistent_upload():
    """GET upload with a random UUID must return 404 from the upload endpoint.

    The assertion on the response body discriminates between a real
    upload-not-found 404 and FastAPI's default route-not-found 404
    (which returns ``{"detail": "Not Found"}`` for unknown routes).
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        response = await client.get(
            "/api/uploads/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404
    body = response.json()
    # Must NOT be the default FastAPI route-not-found body.  When the
    # upload endpoint actually handles the request, it will return its
    # own detail (e.g. "Upload not found"), not the generic "Not Found".
    assert body.get("detail") != "Not Found", (
        f"Got default FastAPI 404 — route /api/uploads/{{upload_id}} doesn't exist yet: {body}"
    )


async def test_get_upload_returns_403_when_not_owner():
    """User A cannot retrieve User B's upload."""
    async with make_client() as client:
        token1, _ = await _auth(client)
        token2, _ = await _auth(client, email="other@test.com", name="Other",
                                 sub="other-sub", token="other-token")

        # User 1 uploads
        video_content = b"\x00" * 500
        files = {"file": ("vid.mp4", io.BytesIO(video_content), "video/mp4")}
        data = {"duration_seconds": "3.0"}

        upload_resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token1}"},
            files=files,
            data=data,
        )
        upload_id = upload_resp.json()["upload_id"]

        # User 2 tries to read it
        response = await client.get(
            f"/api/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )

    assert response.status_code == 403