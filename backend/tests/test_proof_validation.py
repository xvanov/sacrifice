"""Tests for proof schema validation, state-transition enforcement, and audit events.

Covers:
- AC1.1: Valid proof payloads are accepted and validated against goal-type schema
- AC1.2: Invalid proof payloads are rejected before persistence
- AC2.1: Illegal proof/status transitions are rejected
- AC2.2: Test coverage for illegal transition rejection behavior
- AC3.1: Audit events capture accepted proof validation outcomes
- AC3.2: Audit events capture rejected proof validation outcomes
"""

import io
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from app.config import settings
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.proof import ProofSubmission
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _make_db_session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client, email="test@example.com", name="Test User", sub="test-sub-123", token="valid-token"
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


def _create_youtube_goal(title="My YouTube Goal"):
    return {
        "title": title,
        "description": "Record a walkthrough of the app",
        "deadline": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "pledge_amount": 5000,
        "goal_type": "youtube_video",
        "criteria": {
            "min_duration_seconds": 120,
            "video_description": "A walkthrough demo showing how the sacrifice app works",
        },
        "charity_id": "acct_charity123",
    }


async def _create_goal_and_activate(client, token, goal_data=None):
    if goal_data is None:
        goal_data = _create_youtube_goal()
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json=goal_data,
    )
    goal_id = resp.json()["id"]
    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    return goal_id


async def _count_proof_submissions(goal_id):
    db_factory = _make_db_session_factory()
    async with db_factory() as db:
        result = await db.execute(select(ProofSubmission).where(ProofSubmission.goal_id == goal_id))
        return len(result.scalars().all())


async def _get_audit_events(goal_id=None, user_id=None, event_type=None):
    db_factory = _make_db_session_factory()
    async with db_factory() as db:
        stmt = select(AuditEvent)
        if goal_id is not None:
            stmt = stmt.where(AuditEvent.goal_id == goal_id)
        if user_id is not None:
            stmt = stmt.where(AuditEvent.user_id == user_id)
        if event_type is not None:
            stmt = stmt.where(AuditEvent.event_type == event_type)
        result = await db.execute(stmt)
        return result.scalars().all()


# ─── AC1.1 & AC1.2: Schema validation before persistence ──────────────


@pytest.mark.asyncio
async def test_valid_youtube_proof_accepted_and_persisted():
    """AC1.1: Valid YouTube proof payload passes schema validation and is persisted."""
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

        submission_id = uuid.UUID(body["submission_id"])
        db_factory = _make_db_session_factory()
        async with db_factory() as db:
            result = await db.execute(
                select(ProofSubmission).where(ProofSubmission.id == submission_id)
            )
            submission = result.scalar_one()
            assert submission.proof_data.get("video_id") == "dQw4w9WgXcQ"
            assert submission.verification_status == "pending"


@pytest.mark.asyncio
async def test_invalid_youtube_proof_rejected_422_before_persistence():
    """AC1.2: Invalid YouTube proof (bad URL) rejected 422, nothing persisted."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "this-is-not-a-valid-url"},
        )

        assert response.status_code == 422
        count = await _count_proof_submissions(uuid.UUID(goal_id))
        assert count == 0


@pytest.mark.asyncio
async def test_proof_type_mismatch_rejected_400_before_persistence():
    """AC1.2: Proof shaped for wrong goal type rejected 400, nothing persisted.

    Sends api_endpoint-shaped proof (url/method) to a youtube_video goal.
    This triggers ProofTypeMismatch → 400.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "url": "https://example.com/api/health",
                "method": "GET",
            },
        )

        assert response.status_code == 400
        count = await _count_proof_submissions(uuid.UUID(goal_id))
        assert count == 0


# ─── AC2.1 & AC2.2: Illegal proof/status transition rejection ─────────


@pytest.mark.asyncio
async def test_proof_rejected_when_goal_is_draft():
    """AC2.1: Proof submission rejected when goal is 'draft'."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=_create_youtube_goal(),
        )
        goal_id = resp.json()["id"]

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert response.status_code == 400
    assert "draft" in response.json()["detail"]
    assert "active" in response.json()["detail"]


@pytest.mark.asyncio
async def test_proof_rejected_when_goal_is_cancelled():
    """AC2.1: Proof submission rejected when goal is 'cancelled'.

    Only 'active' is in _PROOF_ALLOWED_STATUSES.  Users cannot move from
    active to cancelled through the user endpoint (the accountability
    guard prevents it), so we seed a cancelled goal directly in the DB.
    """
    from app.models.goal import Goal

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        # Directly set the goal to cancelled in the DB.
        db_factory = _make_db_session_factory()
        async with db_factory() as db:
            result = await db.execute(select(Goal).where(Goal.id == uuid.UUID(goal_id)))
            goal = result.scalar_one()
            goal.status = "cancelled"
            await db.commit()

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "cancelled" in detail
    assert "active" in detail


@pytest.mark.asyncio
async def test_proof_rejected_with_explicit_allowed_statuses_in_error():
    """AC2.2: Rejection error message lists the allowed proof submission statuses."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=_create_youtube_goal(),
        )
        goal_id = resp.json()["id"]

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "active" in detail


# ─── AC3.1 & AC3.2: Audit events for accept/reject outcomes ───────────


@pytest.mark.asyncio
async def test_audit_event_captured_for_accepted_proof():
    """AC3.1: Audit event emitted with 'proof_accepted' when validation passes."""
    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = uuid.UUID(await _create_goal_and_activate(client, token))

        with patch("app.workers.youtube.run_youtube_verification_task.delay"):
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )

        assert response.status_code == 202
        submission_id = response.json()["submission_id"]

        events = await _get_audit_events(goal_id=goal_id, event_type="proof_accepted")
        assert len(events) == 1
        event = events[0]
        assert str(event.user_id) == user["id"]
        assert event.details["submission_id"] == submission_id
        assert event.details["goal_type"] == "youtube_video"


@pytest.mark.asyncio
async def test_audit_event_captured_for_rejected_schema_validation():
    """AC3.2: Audit event emitted with 'proof_rejected' on schema validation failure."""
    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = uuid.UUID(await _create_goal_and_activate(client, token))

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "not-a-valid-url"},
        )

        assert response.status_code == 422

        events = await _get_audit_events(goal_id=goal_id, event_type="proof_rejected")
        assert len(events) == 1
        event = events[0]
        assert str(event.user_id) == user["id"]
        assert event.details["reason"] == "schema_validation_failed"
        assert event.details["goal_type"] == "youtube_video"
        assert "error" in event.details


@pytest.mark.asyncio
async def test_audit_event_captured_for_rejected_illegal_transition():
    """AC3.2: Audit event emitted with 'proof_rejected' on illegal status transition."""
    async with make_client() as client:
        token, user = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=_create_youtube_goal(),
        )
        goal_id = uuid.UUID(resp.json()["id"])

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

        assert response.status_code == 400

        events = await _get_audit_events(goal_id=goal_id, event_type="proof_rejected")
        assert len(events) == 1
        event = events[0]
        assert str(event.user_id) == user["id"]
        assert event.details["reason"] == "illegal_transition"
        assert event.details["goal_status"] == "draft"
        assert "active" in event.details["allowed_statuses"]


@pytest.mark.asyncio
async def test_audit_event_captured_for_rejected_proof_type_mismatch():
    """AC3.2: Audit event emitted with 'proof_rejected' on proof type mismatch.

    Sends api_endpoint-shaped proof (url/method) to a youtube_video goal.
    This triggers ProofTypeMismatch with reason 'proof_type_mismatch'.
    """
    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = uuid.UUID(await _create_goal_and_activate(client, token))

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "url": "https://example.com/api/health",
                "method": "GET",
            },
        )

        assert response.status_code == 400

        events = await _get_audit_events(goal_id=goal_id, event_type="proof_rejected")
        assert len(events) == 1
        event = events[0]
        assert str(event.user_id) == user["id"]
        assert event.details["reason"] == "proof_type_mismatch"
        assert event.details["goal_type"] == "youtube_video"


@pytest.mark.asyncio
async def test_multipart_invalid_payload_rejected_before_persistence_and_audited():
    """Multipart payload without goal-type schema fields is rejected + audited."""
    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = uuid.UUID(await _create_goal_and_activate(client, token))

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("proof.png", io.BytesIO(b"proof-bytes"), "image/png"),
            },
        )

        assert response.status_code == 422
        assert await _count_proof_submissions(goal_id) == 0

        events = await _get_audit_events(goal_id=goal_id, event_type="proof_rejected")
        assert len(events) == 1
        event = events[0]
        assert str(event.user_id) == user["id"]
        assert event.details["reason"] == "schema_validation_failed"
        assert event.details["goal_type"] == "youtube_video"


@pytest.mark.asyncio
async def test_multipart_valid_payload_accepted_and_audited():
    """Multipart payload with schema-valid proof_metadata is accepted + audited."""
    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = uuid.UUID(await _create_goal_and_activate(client, token))

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": ("proof.png", io.BytesIO(b"proof-bytes"), "image/png"),
                "proof_metadata": (
                    None,
                    '{"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}',
                ),
            },
        )

        assert response.status_code == 202
        assert await _count_proof_submissions(goal_id) == 1

        events = await _get_audit_events(goal_id=goal_id, event_type="proof_accepted")
        assert len(events) == 1
        event = events[0]
        assert str(event.user_id) == user["id"]
        assert event.details["goal_type"] == "youtube_video"
        assert event.details["submission_id"] == response.json()["submission_id"]


@pytest.mark.asyncio
async def test_no_cross_contamination_of_audit_events_between_users():
    """Audit events for user A's rejection don't leak into user B's queries."""
    async with make_client() as client:
        token_a, user_a = await _auth(client)
        goal_id_a = await _create_goal_and_activate(client, token_a)
        await client.post(
            f"/api/goals/{goal_id_a}/submit-proof",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"youtube_url": "invalid"},
        )

        token_b, user_b = await _auth(
            client,
            email="other@test.com",
            name="Other",
            sub="other-sub",
            token="other-token",
        )
        goal_id_b = await _create_goal_and_activate(client, token_b)
        with patch("app.workers.youtube.run_youtube_verification_task.delay"):
            await client.post(
                f"/api/goals/{goal_id_b}/submit-proof",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )

        events_a = await _get_audit_events(user_id=uuid.UUID(user_a["id"]))
        types_a = {e.event_type for e in events_a}
        assert types_a == {"proof_rejected"}

        events_b = await _get_audit_events(user_id=uuid.UUID(user_b["id"]))
        types_b = {e.event_type for e in events_b}
        assert types_b == {"proof_accepted"}
