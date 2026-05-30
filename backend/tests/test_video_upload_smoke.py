import hashlib
import os
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "e2e", "fixtures")
FIXTURE_PATH = os.path.join(FIXTURE_DIR, "minimal.mp4")

with open(FIXTURE_PATH, "rb") as f:
    FIXTURE_BYTES = f.read()

FIXTURE_SHA256 = hashlib.sha256(FIXTURE_BYTES).hexdigest()
FIXTURE_SIZE = len(FIXTURE_BYTES)


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="smoke-test@example.com", name="Smoke Test",
                sub="smoke-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def test_video_upload_success_returns_201_with_expected_shape():
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("fixture.mp4", FIXTURE_BYTES, "video/mp4")},
            data={"duration_seconds": "12.5"},
        )

    assert resp.status_code == 201

    body = resp.json()

    assert "upload_id" in body
    assert isinstance(body["upload_id"], str)
    assert len(body["upload_id"]) == 36  # UUID4

    assert body["sha256"] == FIXTURE_SHA256
    assert body["size_bytes"] == FIXTURE_SIZE
    assert body["duration_seconds"] == 12.5
    assert body["mime_type"] == "video/mp4"