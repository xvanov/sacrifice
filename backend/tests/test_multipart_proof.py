"""Tests for multipart/form-data proof submission path.

Covers:
- Multipart file upload success (202)
- JSON backward-compatibility preserved
- Invalid multipart requests (missing file, wrong goal state, etc.)
"""

import io
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None, "email_verified": True}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_goal_and_activate(client, token):
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "My YouTube Goal",
            "description": "Record a walkthrough of the app",
            "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {
                "min_duration_seconds": 120,
                "video_description": "A walkthrough demo",
            },
            "charity_id": "acct_charity123",
        },
    )
    goal_id = resp.json()["id"]
    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    return goal_id


# ── Multipart success path ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multipart_proof_submit_returns_202():
    """Multipart file upload with schema-valid proof_metadata is accepted."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.youtube.run_youtube_verification_task.delay") as mock_task:
            mock_task.return_value = None
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": ("evidence.png", io.BytesIO(b"fake-image-data"), "image/png"),
                    "proof_metadata": (
                        None,
                        '{"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}',
                    ),
                },
            )

    assert response.status_code == 202
    body = response.json()
    assert "submission_id" in body
    assert body["verification_status"] == "pending"
    # Multipart path does NOT dispatch Celery.
    mock_task.assert_not_called()


@pytest.mark.asyncio
async def test_multipart_proof_stores_goal_type_data_and_file_evidence():
    """Multipart proof stores schema-validated proof data and file evidence."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("screenshot.png", io.BytesIO(b"png-content-here"), "image/png"),
                "proof_metadata": (
                    None,
                    '{"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}',
                ),
            },
        )

        assert response.status_code == 202

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_resp.status_code == 200
        details = status_resp.json()["verification_details"]
        assert details is not None
        assert details["video_id"] == "dQw4w9WgXcQ"
        assert details["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert details["evidence_file"]["original_filename"] == "screenshot.png"
        assert details["evidence_file"]["mime_type"] == "image/png"
        assert details["evidence_file"]["size_bytes"] == 16  # len(b"png-content-here")
        assert "file_path" in details["evidence_file"]


@pytest.mark.asyncio
async def test_multipart_proof_file_is_written_to_disk():
    """Multipart proof file bytes are persisted on disk at the stored path."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        content = b"proof-image-bytes-here"
        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("proof.jpg", io.BytesIO(content), "image/jpeg"),
                "proof_metadata": (
                    None,
                    '{"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}',
                ),
            },
        )

        assert response.status_code == 202

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        file_path = status_resp.json()["verification_details"]["evidence_file"]["file_path"]

        import os
        assert os.path.exists(file_path)
        with open(file_path, "rb") as f:
            assert f.read() == content


@pytest.mark.asyncio
async def test_multipart_proof_without_metadata_is_rejected():
    """Multipart proof must include schema-valid proof_metadata."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
            },
        )

        assert response.status_code == 422

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_resp.status_code == 404


# ── JSON backward-compatibility ───────────────────────────────────────

@pytest.mark.asyncio
async def test_json_proof_submission_still_works():
    """AC: Existing JSON proof submission behavior is preserved."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.youtube.run_youtube_verification_task.delay") as mock_task:
            mock_task.return_value = None
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )

    assert response.status_code == 202
    body = response.json()
    assert "submission_id" in body
    assert body["verification_status"] == "pending"
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_json_proof_validation_still_works():
    """AC: JSON proof validation (422 for bad URL) still works."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "not-a-url"},
        )

    assert response.status_code == 422


# ── Invalid multipart requests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_multipart_proof_missing_file_returns_422():
    """Multipart request with no file field returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "proof_metadata": (None, '{"note": "no file"}'),
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_multipart_proof_invalid_metadata_json_returns_422():
    """Malformed proof_metadata JSON returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
                "proof_metadata": (None, "not-valid-json"),
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_multipart_proof_goal_not_active_returns_400():
    """Multipart proof on a draft goal returns 400."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Draft Goal",
                "description": "Not active yet",
                "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {
                    "min_duration_seconds": 120,
                    "video_description": "test",
                },
            },
        )
        goal_id = resp.json()["id"]

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
            },
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_multipart_proof_nonexistent_goal_returns_404():
    """Multipart proof on nonexistent goal returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = str(uuid.uuid4())

        response = await client.post(
            f"/api/goals/{fake_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
            },
        )

    assert response.status_code == 404