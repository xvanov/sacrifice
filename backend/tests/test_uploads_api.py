import io
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def temp_media_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "media_dir", tmpdir)
        yield Path(tmpdir)


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


async def _create_goal(client, token):
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Test Goal",
            "description": "For upload tests",
            "deadline": "2026-06-01T00:00:00Z",
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
        },
    )
    return resp.json()["id"]


def _make_mp4_bytes() -> bytes:
    """Minimal valid MP4 bytes (ftyp box only) that libmagic / python-magic
    will identify as video/mp4."""
    return (
        b"\x00\x00\x00\x20\x66\x74\x79\x70\x69\x73\x6f\x6d"
        b"\x00\x00\x02\x00\x69\x73\x6f\x6d\x69\x73\x6f\x32"
        b"\x6d\x70\x34\x31\x00\x00\x00\x08\x66\x72\x65\x65"
    )


# ─── 201: owned goal ────────────────────────────────────────────────


async def test_post_video_upload_returns_201_and_expected_metadata_for_owned_goal():
    """POST /api/uploads/video with an owned goal_id returns 201 and the
    response shape defined in api_spec.md."""
    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_goal(client, token)

        mp4_bytes = _make_mp4_bytes()
        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "12.5", "goal_id": goal_id},
            files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "upload_id" in body
    uuid.UUID(body["upload_id"])  # raises if not a valid UUID
    assert isinstance(body["sha256"], str)
    assert len(body["sha256"]) == 64
    assert body["size_bytes"] == len(mp4_bytes)
    assert body["duration_seconds"] == 12.5
    assert body["mime_type"] == "video/mp4"


# ─── 201: orphan upload ─────────────────────────────────────────────


async def test_post_video_upload_accepts_orphan_upload_without_goal_id():
    """POST /api/uploads/video without goal_id returns 201 and the
    upload is stored as an orphan."""
    async with make_client() as client:
        token, _ = await _auth(client)

        mp4_bytes = _make_mp4_bytes()
        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "5.0"},
            files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["duration_seconds"] == 5.0
    assert "upload_id" in body


# ─── 401: unauthenticated ───────────────────────────────────────────


async def test_post_video_upload_rejects_unauthenticated_request_with_401():
    """POST /api/uploads/video without a valid token returns 401."""
    async with make_client() as client:
        mp4_bytes = _make_mp4_bytes()
        resp = await client.post(
            "/api/uploads/video",
            data={"duration_seconds": "5.0"},
            files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 401


# ─── 403: goal not owned ────────────────────────────────────────────


async def test_post_video_upload_returns_403_for_goal_not_owned_by_user():
    """POST with goal_id belonging to another user returns 403."""
    async with make_client() as client:
        token_a, _ = await _auth(client, email="a@test.com", name="A", sub="sub-a")
        goal_id = await _create_goal(client, token_a)

        token_b, _ = await _auth(client, email="b@test.com", name="B", sub="sub-b", token="tok-b")

        mp4_bytes = _make_mp4_bytes()
        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token_b}"},
            data={"duration_seconds": "5.0", "goal_id": goal_id},
            files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 403


# ─── 415: unsupported media type ────────────────────────────────────


async def test_post_video_upload_returns_415_for_unsupported_media_type():
    """POST with Content-Type text/plain returns 415."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "5.0"},
            files={"file": ("note.txt", io.BytesIO(b"hello world"), "text/plain")},
        )

    assert resp.status_code == 415


# ─── 422: missing file ──────────────────────────────────────────────


async def test_post_video_upload_returns_422_for_missing_file():
    """POST without a file field returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "5.0"},
        )

    assert resp.status_code == 422


# ─── 422: missing duration_seconds ──────────────────────────────────


async def test_post_video_upload_returns_422_for_missing_duration_seconds():
    """POST without duration_seconds returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        mp4_bytes = _make_mp4_bytes()

        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 422


# ─── 422: malformed goal_id ─────────────────────────────────────────


async def test_post_video_upload_returns_422_for_malformed_goal_id():
    """POST with a non-UUID goal_id returns 422, not 403."""
    async with make_client() as client:
        token, _ = await _auth(client)
        mp4_bytes = _make_mp4_bytes()

        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "5.0", "goal_id": "not-a-uuid"},
            files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 422


# ─── 403: nonexistent goal_id (valid UUID, but not in DB) ───────────


async def test_post_video_upload_returns_403_for_nonexistent_goal_id():
    """POST with a valid-UUID goal_id that doesn't exist returns 403 —
    per api_spec.md, any goal_id not owned by the user is forbidden."""
    async with make_client() as client:
        token, _ = await _auth(client)
        mp4_bytes = _make_mp4_bytes()
        fake_goal_id = str(uuid.uuid4())

        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "5.0", "goal_id": fake_goal_id},
            files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


# ─── 413: file exceeds configured limit ─────────────────────────────


async def test_post_video_upload_returns_413_when_file_exceeds_configured_limit(
    monkeypatch,
):
    """POST with a file larger than the configured max returns 413."""
    monkeypatch.setattr(settings, "max_upload_size_bytes", 128)

    async with make_client() as client:
        token, _ = await _auth(client)

        big_data = b"x" * 256  # larger than 128
        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "1.0"},
            files={"file": ("big.mp4", io.BytesIO(big_data), "video/mp4")},
        )

    assert resp.status_code == 413