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


async def _upload_video(client, token, goal_id=None):
    mp4_bytes = (
        b"\x00\x00\x00\x20\x66\x74\x79\x70\x69\x73\x6f\x6d"
        b"\x00\x00\x02\x00\x69\x73\x6f\x6d\x69\x73\x6f\x32"
        b"\x6d\x70\x34\x31\x00\x00\x00\x08\x66\x72\x65\x65"
    )
    data = {"duration_seconds": "7.0"}
    if goal_id:
        data["goal_id"] = goal_id
    resp = await client.post(
        "/api/uploads/video",
        headers={"Authorization": f"Bearer {token}"},
        data=data,
        files={"file": ("proof.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
    )
    return resp.json()


# ─── 200: owner retrieves upload ─────────────────────────────────────


async def test_get_upload_returns_200_and_full_metadata_for_owner():
    """GET /api/uploads/{upload_id} returns 200 with the response shape
    defined in api_spec.md when the owning user requests it."""
    async with make_client() as client:
        token, _ = await _auth(client)
        created = await _upload_video(client, token)

        resp = await client.get(
            f"/api/uploads/{created['upload_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body.keys()) == {
        "upload_id", "goal_id", "sha256", "size_bytes",
        "duration_seconds", "mime_type", "created_at",
    }
    assert body["upload_id"] == created["upload_id"]
    assert body["sha256"] == created["sha256"]
    assert body["size_bytes"] == created["size_bytes"]
    assert body["duration_seconds"] == created["duration_seconds"]
    assert body["mime_type"] == created["mime_type"]
    assert "created_at" in body


# ─── 401: unauthenticated ─────────────────────────────────────────────


async def test_get_upload_returns_401_when_unauthenticated():
    """GET /api/uploads/{upload_id} without a token returns 401."""
    async with make_client() as client:
        resp = await client.get(
            f"/api/uploads/{uuid.uuid4()}",
        )

    assert resp.status_code == 401


# ─── 403: non-owner cannot read upload ───────────────────────────────


async def test_get_upload_returns_403_when_upload_not_owned_by_user():
    """GET /api/uploads/{upload_id} returns 403 when another user owns the upload."""
    async with make_client() as client:
        token_a, _ = await _auth(client, email="a@get.test", name="A", sub="sub-a")
        created = await _upload_video(client, token_a)

        token_b, _ = await _auth(client, email="b@get.test", name="B", sub="sub-b", token="tok-b")

        resp = await client.get(
            f"/api/uploads/{created['upload_id']}",
            headers={"Authorization": f"Bearer {token_b}"},
        )

    assert resp.status_code == 403


# ─── 404: upload not found ────────────────────────────────────────────


async def test_get_upload_returns_404_when_upload_does_not_exist():
    """GET /api/uploads/{upload_id} returns 404 for a valid-UUID upload that
    does not exist in the database."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.get(
            f"/api/uploads/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 404