"""
Smoke test for the video upload API.

Posts a small fixture MP4 to POST /api/uploads/video and asserts the
response shape.  This is a pure HTTP test — it does NOT exercise the
Expo camera-capture component.

Requires the backend to be running at the SACRIFICE_API_URL (default
http://localhost:8000) with a live database.  Uses the dev token
endpoint to avoid requiring a real Google/GitHub OAuth handshake.

Run:
  cd backend
  .venv/bin/pytest tests/test_upload_smoke.py -v -m smoke
"""

import hashlib
import io
import os

import httpx
import pytest

API_URL = os.environ.get("SACRIFICE_API_URL", "http://localhost:8000")


@pytest.mark.smoke
async def test_upload_video_smoke_201():
    """Upload a small valid MP4 fixture and verify the 201 response shape."""
    async with httpx.AsyncClient(base_url=API_URL) as client:
        # Get a dev token
        resp = await client.get("/api/auth/dev/token")
        if resp.status_code != 200:
            pytest.skip("Dev token endpoint not available in this environment")
        token = resp.json()["access_token"]

        # Build a minimal valid MP4-like payload
        video_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42" + b"\x00" * 1024

        files = {"file": ("smoke-test.mp4", io.BytesIO(video_bytes), "video/mp4")}
        data = {"duration_seconds": "3.0"}

        response = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data,
        )

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )

        body = response.json()
        assert "upload_id" in body, f"Missing upload_id in {body}"
        assert "sha256" in body, f"Missing sha256 in {body}"
        assert isinstance(body["sha256"], str)
        assert len(body["sha256"]) == 64
        assert body["size_bytes"] > 0
        assert body["duration_seconds"] == 3.0
        assert body["mime_type"] == "video/mp4"

        # Verify the SHA-256 matches what we uploaded
        expected_sha256 = hashlib.sha256(video_bytes).hexdigest()
        assert body["sha256"] == expected_sha256, (
            f"SHA-256 mismatch: {body['sha256']} != {expected_sha256}"
        )