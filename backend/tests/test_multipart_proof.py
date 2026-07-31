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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.proof import ProofSubmission

# Every test here that submits an acceptable proof must patch the dispatch: a
# real Celery worker with all goal-type tasks registered runs against Redis on
# this host, so an unpatched enqueue would fire live verification work.
DELAY_PATH = "app.workers.youtube.run_youtube_verification_task.delay"


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _get_submission(submission_id) -> ProofSubmission:
    """Load a persisted submission directly, bypassing the API's projection."""
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            result = await db.execute(
                select(ProofSubmission).where(
                    ProofSubmission.id == uuid.UUID(str(submission_id))
                )
            )
            return result.scalar_one()
    finally:
        await engine.dispose()


async def _auth(
    client,
    email="test@example.com",
    name="Test User",
    sub="test-sub-123",
    token="valid-token",
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
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

        with patch(DELAY_PATH) as mock_task:
            mock_task.return_value = None
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": (
                        "evidence.png",
                        io.BytesIO(b"fake-image-data"),
                        "image/png",
                    ),
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
    # Multipart proofs dispatch verification exactly like JSON proofs. They used
    # not to, which is why every file-upload proof stayed "pending" forever.
    mock_task.assert_called_once()
    kwargs = mock_task.call_args.kwargs
    assert kwargs["goal_id_str"] == goal_id
    assert kwargs["submission_id_str"] == body["submission_id"]
    assert kwargs["proof_data"]["video_id"] == "dQw4w9WgXcQ"
    assert kwargs["proof_data"]["evidence_file"]["original_filename"] == "evidence.png"


@pytest.mark.asyncio
async def test_multipart_proof_stores_goal_type_data_and_file_evidence():
    """Multipart proof stores schema-validated proof data and file evidence.

    The submitted proof lives in ``proof_data``. It must NOT be echoed into
    ``verification_details``, which belongs to the verifier and is returned to
    the client by the verification-status endpoint (it carried the absolute
    server path and, for github_repo, the encrypted token).
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch(DELAY_PATH):
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": (
                        "screenshot.png",
                        io.BytesIO(b"png-content-here"),
                        "image/png",
                    ),
                    "proof_metadata": (
                        None,
                        '{"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}',
                    ),
                },
            )

        assert response.status_code == 202
        submission_id = response.json()["submission_id"]

        submission = await _get_submission(submission_id)
        proof_data = submission.proof_data
        assert proof_data["video_id"] == "dQw4w9WgXcQ"
        assert proof_data["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert proof_data["evidence_file"]["original_filename"] == "screenshot.png"
        assert proof_data["evidence_file"]["mime_type"] == "image/png"
        assert (
            proof_data["evidence_file"]["size_bytes"] == 16
        )  # len(b"png-content-here")
        assert "file_path" in proof_data["evidence_file"]

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["verification_status"] == "pending"
        assert status_resp.json()["verification_details"] is None


@pytest.mark.asyncio
async def test_multipart_proof_file_is_written_to_disk():
    """Multipart proof file bytes are persisted on disk at the stored path."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        content = b"proof-image-bytes-here"
        with patch(DELAY_PATH):
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

        # The stored path is recorded on the submission's proof_data (it is
        # deliberately not exposed through the verification-status endpoint).
        submission = await _get_submission(response.json()["submission_id"])
        file_path = submission.proof_data["evidence_file"]["file_path"]

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

        with patch(DELAY_PATH) as mock_task:
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
                },
            )

        assert response.status_code == 422
        # A rejected proof must never reach the verifier.
        mock_task.assert_not_called()

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

        with patch(DELAY_PATH) as mock_task:
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

        with patch(DELAY_PATH) as mock_task:
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "proof_metadata": (None, '{"note": "no file"}'),
                },
            )

    assert response.status_code == 422
    mock_task.assert_not_called()


@pytest.mark.asyncio
async def test_multipart_proof_invalid_metadata_json_returns_422():
    """Malformed proof_metadata JSON returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch(DELAY_PATH) as mock_task:
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
                    "proof_metadata": (None, "not-valid-json"),
                },
            )

    assert response.status_code == 422
    mock_task.assert_not_called()


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
                "deadline": (
                    datetime.now(timezone.utc) + timedelta(days=7)
                ).isoformat(),
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {
                    "min_duration_seconds": 120,
                    "video_description": "test",
                },
            },
        )
        goal_id = resp.json()["id"]

        with patch(DELAY_PATH) as mock_task:
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
                },
            )

    assert response.status_code == 400
    mock_task.assert_not_called()


@pytest.mark.asyncio
async def test_multipart_proof_nonexistent_goal_returns_404():
    """Multipart proof on nonexistent goal returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = str(uuid.uuid4())

        with patch(DELAY_PATH) as mock_task:
            response = await client.post(
                f"/api/goals/{fake_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": ("evidence.png", io.BytesIO(b"data"), "image/png"),
                },
            )

    assert response.status_code == 404
    mock_task.assert_not_called()
