"""
Smoke test for POST /api/uploads/video success path.

Uploads a fixture video via multipart/form-data, asserts 201 and
expected response shape per api_spec.md.
"""

import hashlib
import os
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


# Minimal valid MP4 bytes (ftyp + moov atoms) so the server can inspect mime type.
# This is NOT a playable video — it is a fixture for the upload contract smoke test.
def _minimal_mp4() -> bytes:
    import struct

    def atom(typ: bytes, data: bytes) -> bytes:
        return struct.pack(">I", 8 + len(data)) + typ + data

    ftyp = atom(b"ftyp", b"isom\x00\x00\x00\x00isom")
    moov = atom(b"moov", b"")
    return ftyp + moov


FIXTURE_MP4_BYTES = _minimal_mp4()
FIXTURE_SHA256 = hashlib.sha256(FIXTURE_MP4_BYTES).hexdigest()
FIXTURE_SIZE = len(FIXTURE_MP4_BYTES)


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    from unittest.mock import patch
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


@pytest.mark.smoke
async def test_video_upload_success_returns_201_with_expected_shape():
    """Upload a fixture MP4 via multipart/form-data and assert the response shape.

    This test MUST fail (RED) before the POST /api/uploads/video endpoint exists.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            tf.write(FIXTURE_MP4_BYTES)
            tmp_path = tf.name

        try:
            response = await client.post(
                "/api/uploads/video",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": ("fixture.mp4", open(tmp_path, "rb"), "video/mp4")},
                data={"duration_seconds": "12.5"},
            )
        finally:
            os.unlink(tmp_path)

    assert response.status_code == 201, (
        f"Expected 201, got {response.status_code}: {response.text}"
    )

    body = response.json()

    assert "upload_id" in body, f"Missing upload_id in {body}"
    assert isinstance(body["upload_id"], str)

    assert body["sha256"] == FIXTURE_SHA256, (
        f"sha256 mismatch: {body.get('sha256')} vs {FIXTURE_SHA256}"
    )

    assert body["size_bytes"] == FIXTURE_SIZE, (
        f"size_bytes mismatch: {body.get('size_bytes')} vs {FIXTURE_SIZE}"
    )

    assert body["duration_seconds"] == 12.5, (
        f"duration_seconds mismatch: {body.get('duration_seconds')}"
    )

    assert body["mime_type"] == "video/mp4", (
        f"mime_type mismatch: {body.get('mime_type')}"
    )