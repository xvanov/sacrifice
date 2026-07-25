"""Tests for verification of file-upload (multipart) proof submissions.

The multipart submit-proof path used to persist a submission and stop: it never
called ``dispatch_verification``, so every file-upload proof stayed in
``verification_status="pending"`` forever while JSON proofs verified normally.
It also pre-populated ``verification_details`` with an echo of the submitted
proof, exposing encrypted github tokens and absolute server paths through
``GET /api/goals/{id}/verification-status``.

Covers:
- Verification is dispatched for a multipart proof, with the evidence file
- A file-upload proof reaches a terminal verified/failed state
- ``verification_details`` is NULL while a submission is pending (no echo)
- The multipart error contracts (422/400/413) are unchanged
- The upload size cap at its boundary
"""

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.goal_types import registry as goal_type_registry
from app.main import app
from app.models.goal import Goal
from app.models.proof import ProofSubmission

pytestmark = pytest.mark.asyncio

# Golden Gate Bridge midpoint — the geolocation target used below.
GG_LAT, GG_LON = 37.8199, -122.4783

YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


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
        return resp.json()["access_token"]


async def _create_active_goal(
    client, token, goal_type, criteria, title="Upload proof goal"
):
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": title,
            "description": "File-upload proof test",
            "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "pledge_amount": 5000,
            "goal_type": goal_type,
            "criteria": criteria,
            "charity_id": "acct_charity123",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    goal_id = resp.json()["id"]
    resp = await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    assert resp.status_code == 200, resp.text
    return goal_id


async def _create_active_youtube_goal(client, token):
    return await _create_active_goal(
        client,
        token,
        "youtube_video",
        {"min_duration_seconds": 120, "video_description": "A walkthrough demo"},
    )


def _multipart(
    metadata: dict | str | None,
    filename="evidence.png",
    content=b"fake-image-data",
    mime="image/png",
    include_file=True,
):
    files: dict = {}
    if include_file:
        files["file"] = (filename, io.BytesIO(content), mime)
    if metadata is not None:
        raw = metadata if isinstance(metadata, str) else json.dumps(metadata)
        files["proof_metadata"] = (None, raw)
    return files


# ── The regression: multipart proofs must be dispatched for verification ──


async def test_multipart_proof_dispatches_verification_with_evidence_file():
    """A file-upload proof enqueues verification with the evidence file attached.

    Fails on the old code: ``_multipart_proof_submission`` returned right after
    persisting, so ``.delay`` was never called at all.
    """
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)

        with patch(
            "app.workers.youtube.run_youtube_verification_task.delay"
        ) as mock_delay:
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files=_multipart({"youtube_url": YT_URL}, filename="screenshot.png"),
            )

    assert resp.status_code == 202, resp.text
    assert resp.json()["verification_status"] == "pending"

    mock_delay.assert_called_once()
    kwargs = mock_delay.call_args.kwargs
    assert kwargs["goal_id_str"] == goal_id
    assert kwargs["submission_id_str"] == resp.json()["submission_id"]
    assert kwargs["proof_data"]["video_id"] == "dQw4w9WgXcQ"
    evidence = kwargs["proof_data"]["evidence_file"]
    assert evidence["original_filename"] == "screenshot.png"
    assert evidence["mime_type"] == "image/png"
    assert evidence["file_path"].endswith(".png")
    # The goal-type criteria must survive to the verifier (the multipart path
    # used to drop prepared["criteria_data"] entirely).
    assert kwargs["criteria_data"]["min_duration_seconds"] == 120
    # Celery serializes task arguments as JSON: anything non-serializable here
    # would raise at enqueue time in production.
    json.dumps(
        {"proof_data": kwargs["proof_data"], "criteria_data": kwargs["criteria_data"]}
    )


async def test_multipart_proof_reaches_terminal_verified_state():
    """A geolocation proof submitted as multipart resolves out of ``pending``.

    Drives the real worker entry point with exactly the arguments the route
    dispatched, which is what the Celery task does in the worker process.

    Fails on the old code twice over: nothing is dispatched (so there are no
    arguments to drive the worker with), and the submission stays ``pending``.
    """
    dispatched: dict = {}

    def _spy(**kwargs):
        dispatched.update(kwargs)

    engine, session_factory = _session_factory()
    try:
        async with make_client() as client:
            token = await _auth(client)
            goal_id = await _create_active_goal(
                client,
                token,
                "geolocation",
                {
                    "target_latitude": GG_LAT,
                    "target_longitude": GG_LON,
                    "radius_m": 150,
                },
            )

            with patch(
                "app.workers.geolocation.run_geolocation_verification_task.delay",
                side_effect=_spy,
            ):
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    files=_multipart(
                        {"latitude": GG_LAT, "longitude": GG_LON, "accuracy_m": 10.0},
                        filename="checkin.jpg",
                        content=b"jpeg-bytes",
                        mime="image/jpeg",
                    ),
                )

            assert resp.status_code == 202, resp.text
            assert dispatched, "multipart proof did not dispatch verification"

            from app.workers.geolocation import run_geolocation_verification

            async with session_factory() as db:
                await run_geolocation_verification(
                    goal_id=uuid.UUID(dispatched["goal_id_str"]),
                    submission_id=uuid.UUID(dispatched["submission_id_str"]),
                    proof_data=dispatched["proof_data"],
                    criteria_data=dispatched["criteria_data"],
                    db=db,
                )

                result = await db.execute(
                    select(ProofSubmission).where(
                        ProofSubmission.id == uuid.UUID(resp.json()["submission_id"])
                    )
                )
                submission = result.scalar_one()
                await db.refresh(submission)
                assert submission.verification_status == "verified"
                assert submission.verification_details["location_passed"] is True

                # The evidence file is retained on the submission and on disk.
                evidence = submission.proof_data["evidence_file"]
                assert evidence["original_filename"] == "checkin.jpg"
                from pathlib import Path

                assert Path(evidence["file_path"]).read_bytes() == b"jpeg-bytes"

                result = await db.execute(
                    select(Goal).where(Goal.id == uuid.UUID(goal_id))
                )
                goal = result.scalar_one()
                await db.refresh(goal)
                assert goal.status == "verified"

            # And the endpoint no longer reports pending.
            status_resp = await client.get(
                f"/api/goals/{goal_id}/verification-status",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert status_resp.json()["verification_status"] == "verified"
    finally:
        await engine.dispose()


# ── verification_details must not echo the submitted proof ────────────────


async def test_pending_multipart_submission_has_no_verification_details():
    """A freshly created pending submission has no verification details.

    Fails on the old code, which set ``verification_details=proof_data``.
    """
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)

        with patch("app.workers.youtube.run_youtube_verification_task.delay"):
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files=_multipart({"youtube_url": YT_URL}),
            )
        assert resp.status_code == 202, resp.text

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_resp.status_code == 200
        payload = status_resp.json()
        assert payload["verification_status"] == "pending"
        assert payload["verification_details"] is None


async def test_pending_multipart_submission_leaks_no_token_or_server_path():
    """No github token and no server filesystem path reach the status endpoint.

    ``github_repo`` proof data carries an encrypted ``github_token``, and every
    multipart submission carries an absolute ``file_path``. Both were echoed
    into ``verification_details`` by the old code and returned to the client.
    """
    goal_type = goal_type_registry.get_type("github_repo")
    dispatched: dict = {}

    def _spy(**kwargs):
        dispatched.update(kwargs)

    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_goal(
            client,
            token,
            "github_repo",
            {"repo_url": "https://github.com/test/repo", "conditions": []},
        )

        with patch.object(goal_type, "dispatch_verification", _spy):
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files=_multipart(
                    {
                        "repo_url": "https://github.com/test/repo",
                        "github_token": "ghp_supersecrettoken",
                    },
                    filename="screenshot.png",
                ),
            )
        assert resp.status_code == 202, resp.text

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = status_resp.text
        assert status_resp.json()["verification_details"] is None
        assert "github_token" not in body
        assert "ghp_supersecrettoken" not in body
        assert "file_path" not in body
        assert settings.media_dir not in body

    # The token still reaches the verifier through the dispatch (encrypted),
    # so suppressing the echo did not break verification.
    assert dispatched, "multipart proof did not dispatch verification"
    assert dispatched["proof_data"]["github_token"] != "ghp_supersecrettoken"
    assert (
        dispatched["proof_data"]["evidence_file"]["original_filename"]
        == "screenshot.png"
    )


async def test_pending_json_submission_has_no_verification_details_echo():
    """The JSON path must not pre-populate verification details either.

    It never did — ``ProofSubmission`` is created without the column, leaving it
    NULL until the verifier writes the real result. This pins that down so the
    echo cannot be reintroduced on the path that was always correct.
    """
    goal_type = goal_type_registry.get_type("github_repo")

    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_goal(
            client,
            token,
            "github_repo",
            {"repo_url": "https://github.com/test/repo", "conditions": []},
        )

        with patch.object(goal_type, "dispatch_verification", lambda **kw: None):
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "repo_url": "https://github.com/test/repo",
                    "github_token": "ghp_supersecrettoken",
                },
            )
        assert resp.status_code == 202, resp.text

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_resp.json()["verification_details"] is None
        assert "github_token" not in status_resp.text
        assert "ghp_supersecrettoken" not in status_resp.text


# ── unchanged error contracts (fences, not regressions) ───────────────────


async def test_multipart_missing_file_returns_422():
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart({"youtube_url": YT_URL}, include_file=False),
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "No file provided in multipart proof submission"


async def test_multipart_missing_metadata_returns_422():
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart(None),
        )
    assert resp.status_code == 422
    assert (
        resp.json()["detail"] == "proof_metadata is required and must be a JSON object"
    )


async def test_multipart_unparseable_metadata_returns_422():
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart("not-valid-json"),
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "proof_metadata must be valid JSON"


async def test_multipart_non_object_metadata_returns_422():
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart("[1, 2, 3]"),
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "proof_metadata must be a JSON object"


async def test_multipart_metadata_not_matching_schema_returns_422():
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart({"latitude": "not-a-number"}),
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "proof_metadata must match ProofSubmissionCreate"


async def test_multipart_invalid_proof_for_goal_type_returns_422():
    """ProofValidationError (bad URL for the right goal type) stays 422."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart({"youtube_url": "not-a-url"}),
        )
    assert resp.status_code == 422


async def test_multipart_wrong_goal_type_proof_returns_400():
    """ProofTypeMismatch (proof shaped for another goal type) stays 400."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart({"url": "https://example.com/health", "method": "GET"}),
        )
    assert resp.status_code == 400


async def test_multipart_no_submission_persisted_on_rejection():
    """A rejected multipart proof leaves no submission behind."""
    engine, session_factory = _session_factory()
    try:
        async with make_client() as client:
            token = await _auth(client)
            goal_id = await _create_active_youtube_goal(client, token)
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files=_multipart({"youtube_url": "not-a-url"}),
            )
            assert resp.status_code == 422

            async with session_factory() as db:
                result = await db.execute(
                    select(ProofSubmission).where(
                        ProofSubmission.goal_id == uuid.UUID(goal_id)
                    )
                )
                assert result.scalars().all() == []
    finally:
        await engine.dispose()


async def test_multipart_dispatch_failure_still_returns_202_pending():
    """A broker outage must not 500 the submitter (parity with the JSON path)."""
    goal_type = goal_type_registry.get_type("youtube_video")

    def _boom(**kwargs):
        raise RuntimeError("broker unavailable")

    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        with patch.object(goal_type, "dispatch_verification", _boom):
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files=_multipart({"youtube_url": YT_URL}),
            )
    assert resp.status_code == 202, resp.text
    assert resp.json()["verification_status"] == "pending"


# ── upload size cap ──────────────────────────────────────────────────────


async def test_multipart_proof_file_at_size_cap_is_accepted():
    """Boundary: exactly ``max_upload_size_bytes`` is allowed."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        with patch("app.routes.goals.settings.max_upload_size_bytes", 32):
            with patch("app.workers.youtube.run_youtube_verification_task.delay"):
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    files=_multipart({"youtube_url": YT_URL}, content=b"x" * 32),
                )
    assert resp.status_code == 202, resp.text


async def test_multipart_proof_file_over_size_cap_returns_413():
    """One byte over ``max_upload_size_bytes`` is rejected with 413.

    Fails on the old code, which read the whole upload into memory with no cap.
    """
    engine, session_factory = _session_factory()
    try:
        async with make_client() as client:
            token = await _auth(client)
            goal_id = await _create_active_youtube_goal(client, token)
            with patch("app.routes.goals.settings.max_upload_size_bytes", 32):
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    files=_multipart({"youtube_url": YT_URL}, content=b"x" * 33),
                )
            assert resp.status_code == 413, resp.text
            assert resp.json()["detail"] == "File exceeds configured max size"

            async with session_factory() as db:
                result = await db.execute(
                    select(ProofSubmission).where(
                        ProofSubmission.goal_id == uuid.UUID(goal_id)
                    )
                )
                assert result.scalars().all() == []
    finally:
        await engine.dispose()


# ── payload guard parity with the JSON path ──────────────────────────────


async def test_multipart_deeply_nested_metadata_returns_422():
    """proof_metadata is subject to the same nesting guard as a JSON body.

    Fails on the old code, where moving the payload into the multipart field
    bypassed ``validate_json_payload`` entirely.
    """
    nested: dict = {"env_vars": {}}
    node = nested["env_vars"]
    for _ in range(15):
        node["deeper"] = {}
        node = node["deeper"]

    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files=_multipart(nested),
        )
    assert resp.status_code == 422
    assert "nesting depth" in resp.json()["detail"]


# ── stored filename hardening ────────────────────────────────────────────


async def test_multipart_proof_hostile_filename_gets_safe_extension():
    """A client-supplied filename never picks the stored extension verbatim."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id = await _create_active_youtube_goal(client, token)

        with patch(
            "app.workers.youtube.run_youtube_verification_task.delay"
        ) as mock_delay:
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                files=_multipart(
                    {"youtube_url": YT_URL},
                    filename="evidence.p n%00g",
                ),
            )
        assert resp.status_code == 202, resp.text

    evidence = mock_delay.call_args.kwargs["proof_data"]["evidence_file"]
    from pathlib import Path

    stored = Path(evidence["file_path"])
    assert stored.suffix == ".bin"
    assert stored.parent == Path(settings.media_dir) / "proofs"
    # The original name is still recorded for the user, just not used on disk.
    assert evidence["original_filename"] == "evidence.p n%00g"
