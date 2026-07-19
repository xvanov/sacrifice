import hashlib
import os
import uuid

import pytest
from app import config
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _writable_media_dir(tmp_path, monkeypatch):
    """Point the media root at a writable tmp dir — the default
    /var/sacrifice/media is not writable in the test environment."""
    monkeypatch.setattr(config.settings, "sacrifice_media_dir", str(tmp_path))


FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "e2e", "fixtures", "minimal.mp4")

with open(FIXTURE_PATH, "rb") as f:
    FIXTURE_BYTES = f.read()

FIXTURE_SHA256 = hashlib.sha256(FIXTURE_BYTES).hexdigest()
FIXTURE_SIZE = len(FIXTURE_BYTES)


def _make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _get_token(client, email="smoke-test@example.com"):
    resp = await client.get(f"/api/auth/dev/token?email={email}")
    return resp.json()["access_token"]


async def test_video_upload_success_returns_201_with_expected_shape():
    async with _make_client() as client:
        token = await _get_token(client)

        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("fixture.mp4", FIXTURE_BYTES, "video/mp4")},
            data={"duration_seconds": "12.5"},
        )

    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text[:500]}"

    body = resp.json()

    # Top-level keys per api_spec.md
    assert set(body.keys()) == {
        "upload_id",
        "sha256",
        "size_bytes",
        "duration_seconds",
        "mime_type",
    }, f"unexpected response keys: {set(body.keys())}"

    # upload_id must be a valid UUID
    uuid.UUID(body["upload_id"])

    assert body["sha256"] == FIXTURE_SHA256
    assert body["size_bytes"] == FIXTURE_SIZE
    assert body["duration_seconds"] == 12.5
    assert body["mime_type"] == "video/mp4"
