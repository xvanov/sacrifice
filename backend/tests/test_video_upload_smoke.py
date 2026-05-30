import hashlib
import os

from httpx import ASGITransport, AsyncClient

from app.main import app

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "e2e", "fixtures", "minimal.mp4"
)

with open(FIXTURE_PATH, "rb") as f:
    FIXTURE_BYTES = f.read()

FIXTURE_SHA256 = hashlib.sha256(FIXTURE_BYTES).hexdigest()
FIXTURE_SIZE = len(FIXTURE_BYTES)


def _make_client():
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


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
        "upload_id", "sha256", "size_bytes", "duration_seconds", "mime_type",
    }, f"unexpected response keys: {set(body.keys())}"

    # upload_id must be a valid UUID4 string
    upload_id = body["upload_id"]
    assert isinstance(upload_id, str)
    assert len(upload_id) == 36
    # UUID4 hex format: 8-4-4-4-12
    parts = upload_id.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    assert len(parts[3]) == 4
    assert len(parts[4]) == 12

    assert body["sha256"] == FIXTURE_SHA256
    assert body["size_bytes"] == FIXTURE_SIZE
    assert body["duration_seconds"] == 12.5
    assert body["mime_type"] == "video/mp4"