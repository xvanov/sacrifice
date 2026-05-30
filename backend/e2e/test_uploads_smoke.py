"""Live-stack E2E smoke test for POST /api/uploads/video.

Performs a real multipart upload against the RUNNING backend and asserts
the 201 response shape as specified in api_spec.md.

Requires the full live stack: backend + database.
Usage:
  make up
  cd backend && .venv/bin/python e2e/test_uploads_smoke.py
"""

import io
import os
import sys
import uuid

import httpx

API_URL = os.environ.get("SACRIFICE_API_URL", "http://localhost:8000")


def _make_mp4_bytes() -> bytes:
    """Minimal valid MP4 bytes (ftyp box only) that libmagic identifies as video/mp4."""
    return (
        b"\x00\x00\x00\x20\x66\x74\x79\x70\x69\x73\x6f\x6d"
        b"\x00\x00\x02\x00\x69\x73\x6f\x6d\x69\x73\x6f\x32"
        b"\x6d\x70\x34\x31\x00\x00\x00\x08\x66\x72\x65\x65"
    )


def _auth(client: httpx.Client) -> str:
    """Authenticate via Google OAuth mock and return an access token."""
    resp = client.post(
        f"{API_URL}/api/auth/google",
        json={"token": "e2e-uploads-token"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Auth failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def test_smoke_post_video_upload_returns_201():
    """Live-stack E2E: authenticated multipart upload returns 201 with
    the exact response shape from api_spec.md."""
    with httpx.Client(timeout=30) as client:
        token = _auth(client)

        mp4_bytes = _make_mp4_bytes()
        resp = client.post(
            f"{API_URL}/api/uploads/video",
            headers={"Authorization": f"Bearer {token}"},
            data={"duration_seconds": "7.25"},
            files={"file": ("smoke.mp4", io.BytesIO(mp4_bytes), "video/mp4")},
        )

    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    body = resp.json()

    # Exact keys per api_spec.md
    assert set(body.keys()) == {
        "upload_id", "sha256", "size_bytes", "duration_seconds", "mime_type",
    }

    # Type / format assertions
    uuid.UUID(body["upload_id"])
    assert isinstance(body["sha256"], str)
    assert len(body["sha256"]) == 64  # hex-encoded SHA-256
    assert body["size_bytes"] == len(mp4_bytes)
    assert body["duration_seconds"] == 7.25
    assert body["mime_type"] == "video/mp4"

    print(f"PASS: upload_id={body['upload_id']} sha256={body['sha256'][:12]}...")


if __name__ == "__main__":
    print(f"Testing against {API_URL}")
    try:
        test_smoke_post_video_upload_returns_201()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)