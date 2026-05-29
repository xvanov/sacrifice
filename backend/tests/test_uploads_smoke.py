"""Smoke (E2E-style HTTP) test for POST /api/uploads/video.

Performs a real multipart upload against the running API and asserts the
201 response shape as specified in api_spec.md.  This is a pure HTTP test;
it does NOT exercise the Expo capture component.

Requires the full live stack to be up (backend + database).
"""

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


def _make_mp4_bytes() -> bytes:
    return (
        b"\x00\x00\x00\x20\x66\x74\x79\x70\x69\x73\x6f\x6d"
        b"\x00\x00\x02\x00\x69\x73\x6f\x6d\x69\x73\x6f\x32"
        b"\x6d\x70\x34\x31\x00\x00\x00\x08\x66\x72\x65\x65"
    )


async def test_smoke_post_video_upload_via_api_returns_201_response_shape():
    """End-to-end smoke test: authenticated multipart upload returns 201
    with the exact response shape from api_spec.md."""
    async with make_client() as client:
        # Authenticate
        with patch("app.routes.auth.verify_google_token") as mock:
            mock.return_value = {
                "email": "smoke@test.com",
                "name": "Smoke User",
                "sub": "smoke-sub",
                "picture": None,
            }
            login_resp = await client.post(
                "/api/auth/google", json={"token": "valid-token"}
            )
        token = login_resp.json()["access_token"]

        mp4_bytes = _make_mp4_bytes()
        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "7.25"},
            files={"file": ("smoke.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    body = resp.json()

    # Exact keys per api_spec.md
    assert set(body.keys()) == {"upload_id", "sha256", "size_bytes", "duration_seconds", "mime_type"}

    # Type / format assertions
    uuid.UUID(body["upload_id"])
    assert isinstance(body["sha256"], str)
    assert len(body["sha256"]) == 64  # hex-encoded SHA-256
    assert body["size_bytes"] == len(mp4_bytes)
    assert body["duration_seconds"] == 7.25
    assert body["mime_type"] == "video/mp4"