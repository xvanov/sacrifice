import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from app.main import app
from app.models.goal import Goal
from app.models.proof import ProofSubmission
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client,
    email="test@example.com",
    name="Test User",
    sub="test-sub-123",
    token="valid-token",
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": name,
            "sub": sub,
            "picture": None,
            "email_verified": True,
        }
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


def _create_youtube_goal(title="My YouTube Goal"):
    return {
        "title": title,
        "description": "Record a walkthrough of the app",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "pledge_amount": 5000,
        "goal_type": "youtube_video",
        "criteria": {
            "min_duration_seconds": 120,
            "video_description": "A walkthrough demo showing how the sacrifice app works",
        },
        "charity_id": "acct_charity123",
    }


async def _create_goal_and_activate(client, token):
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json=_create_youtube_goal(),
    )
    goal_id = resp.json()["id"]
    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    return goal_id


# ─── POST /api/goals/{id}/submit-proof ──────────────────────────────


@pytest.mark.asyncio
async def test_submit_proof_valid_url_returns_202():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch(
            "app.workers.youtube.run_youtube_verification_task.delay"
        ) as mock_task:
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
async def test_submit_proof_returns_422_for_invalid_url():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "not-a-url"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_proof_returns_404_for_nonexistent_goal():
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = str(uuid.uuid4())

        response = await client.post(
            f"/api/goals/{fake_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_submit_proof_returns_404_for_other_users_goal():
    async with make_client() as client:
        token1, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token1)

        token2, _ = await _auth(
            client,
            email="other@test.com",
            name="Other",
            sub="other-sub",
            token="other-token",
        )
        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token2}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_submit_proof_returns_400_when_goal_not_active():
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


@pytest.mark.asyncio
async def test_submit_proof_returns_400_for_non_youtube_goal():
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "API Goal",
                "deadline": (
                    datetime.now(timezone.utc) + timedelta(days=7)
                ).isoformat(),
                "pledge_amount": 5000,
                "goal_type": "api_endpoint",
                "criteria": {
                    "method": "GET",
                    "url": "https://example.com/health",
                    "expected_status": 200,
                },
            },
        )
        api_goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{api_goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        response = await client.post(
            f"/api/goals/{api_goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert response.status_code == 400


# ─── GET /api/goals/{id}/verification-status ────────────────────────


@pytest.mark.asyncio
async def test_verification_status_returns_pending_after_submission():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.youtube.run_youtube_verification_task.delay"):
            submit_resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )
            submission_id = submit_resp.json()["submission_id"]

            status_resp = await client.get(
                f"/api/goals/{goal_id}/verification-status",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["submission_id"] == submission_id
    assert body["verification_status"] == "pending"


# ─── YouTube Verification Logic (mocked external APIs, no DB) ──────


@pytest.mark.asyncio
async def test_verification_video_shorter_than_min_duration_fails():
    from app.workers.youtube import verify_youtube_content

    proof_data = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    }
    criteria_data = {
        "min_duration_seconds": 300,
        "video_description": "A detailed walkthrough",
    }

    with patch(
        "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
    ) as mock_meta:
        mock_meta.return_value = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Rick Astley",
            "duration_seconds": 30,
        }

        result = await verify_youtube_content(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    details_lower = str(result["verification_details"]).lower()
    assert "duration" in details_lower

    mock_meta.assert_awaited_once_with("dQw4w9WgXcQ")


@pytest.mark.asyncio
async def test_verification_video_matching_content_verified():
    from app.workers.youtube import verify_youtube_content

    proof_data = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    }
    criteria_data = {
        "min_duration_seconds": 60,
        "video_description": "A walkthrough of the sacrifice accountability app",
    }

    with (
        patch(
            "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
        ) as mock_meta,
        patch(
            "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
        ) as mock_transcript,
        patch(
            "app.workers.youtube.judge_transcript_content", new_callable=AsyncMock
        ) as mock_judge,
    ):
        mock_meta.return_value = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Sacrifice App Walkthrough",
            "duration_seconds": 120,
        }
        mock_transcript.return_value = "This is a walkthrough of the sacrifice app showing how to create goals and submit proof."
        mock_judge.return_value = {
            "authentic": True,
            "reasoning": "The transcript covers the goal description topics.",
        }

        result = await verify_youtube_content(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    assert result["verification_details"]["duration_passed"] is True
    assert result["verification_details"]["content_passed"] is True
    assert result["verification_details"]["llm_reasoning"] is not None

    mock_meta.assert_awaited_once()
    mock_transcript.assert_awaited_once()
    mock_judge.assert_awaited_once()


@pytest.mark.asyncio
async def test_verification_video_non_matching_content_fails():
    from app.workers.youtube import verify_youtube_content

    proof_data = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    }
    criteria_data = {
        "min_duration_seconds": 60,
        "video_description": "A walkthrough of the sacrifice accountability app",
    }

    with (
        patch(
            "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
        ) as mock_meta,
        patch(
            "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
        ) as mock_transcript,
        patch(
            "app.workers.youtube.judge_transcript_content", new_callable=AsyncMock
        ) as mock_judge,
    ):
        mock_meta.return_value = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Something Unrelated",
            "duration_seconds": 120,
        }
        mock_transcript.return_value = (
            "Never gonna give you up, never gonna let you down..."
        )
        mock_judge.return_value = {
            "authentic": False,
            "reasoning": "The transcript is about a music video, not the sacrifice app.",
        }

        result = await verify_youtube_content(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    assert result["verification_details"]["duration_passed"] is True
    assert result["verification_details"]["content_passed"] is False


@pytest.mark.asyncio
async def test_verification_unavailable_transcript_fails():
    from app.workers.youtube import verify_youtube_content

    proof_data = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    }
    criteria_data = {
        "min_duration_seconds": 60,
        "video_description": "A walkthrough",
    }

    with (
        patch(
            "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
        ) as mock_meta,
        patch(
            "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
        ) as mock_transcript,
    ):
        mock_meta.return_value = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Sacrifice App Walkthrough",
            "duration_seconds": 120,
        }
        mock_transcript.side_effect = ValueError("Transcript not available")

        result = await verify_youtube_content(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    assert "transcript" in str(result["verification_details"]).lower()


# ─── Goal status transitions (via DB) ───────────────────────────────


@pytest.mark.asyncio
async def test_verification_goal_status_transitions_to_verified():
    from app.config import settings
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    local_engine = create_async_engine(settings.database_url, echo=False)
    local_session_factory = async_sessionmaker(
        local_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.youtube.run_youtube_verification_task.delay"):
            submit_resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )
            submission_id = submit_resp.json()["submission_id"]

        async with local_session_factory() as db:
            result = await db.execute(
                select(ProofSubmission).where(ProofSubmission.id == submission_id)
            )
            submission = result.scalar_one()

            result = await db.execute(select(Goal).where(Goal.id == goal_id))
            goal = result.scalar_one()

            criteria = await db.execute(select(Goal.criteria).where(Goal.id == goal_id))

            with (
                patch(
                    "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
                ) as mock_meta,
                patch(
                    "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
                ) as mock_transcript,
                patch(
                    "app.workers.youtube.judge_transcript_content",
                    new_callable=AsyncMock,
                ) as mock_judge,
            ):
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Sacrifice Walkthrough",
                    "duration_seconds": 180,
                }
                mock_transcript.return_value = (
                    "A complete walkthrough of the sacrifice app..."
                )
                mock_judge.return_value = {
                    "authentic": True,
                    "reasoning": "Matches the goal description.",
                }

                from app.workers.youtube import run_youtube_verification

                criteria_data = {
                    "min_duration_seconds": 120,
                    "video_description": "A walkthrough demo showing how the sacrifice app works",
                }
                await run_youtube_verification(
                    goal_id=goal.id,
                    submission_id=submission.id,
                    proof_data=submission.proof_data,
                    criteria_data=criteria_data,
                    db=db,
                )

            await db.refresh(goal)
            await db.refresh(submission)

            assert goal.status == "verified"
            assert submission.verification_status == "verified"

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    body = status_resp.json()
    assert body["verification_status"] == "verified"
    await local_engine.dispose()


@pytest.mark.asyncio
async def test_verification_goal_status_transitions_to_failed():
    from app.config import settings
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    local_engine = create_async_engine(settings.database_url, echo=False)
    local_session_factory = async_sessionmaker(
        local_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.youtube.run_youtube_verification_task.delay"):
            submit_resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )
            submission_id = submit_resp.json()["submission_id"]

        async with local_session_factory() as db:
            result = await db.execute(
                select(ProofSubmission).where(ProofSubmission.id == submission_id)
            )
            submission = result.scalar_one()

            result = await db.execute(select(Goal).where(Goal.id == goal_id))
            goal = result.scalar_one()

            with (
                patch(
                    "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
                ) as mock_meta,
                patch(
                    "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
                ) as mock_transcript,
                patch(
                    "app.workers.youtube.judge_transcript_content",
                    new_callable=AsyncMock,
                ) as mock_judge,
            ):
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Unrelated",
                    "duration_seconds": 180,
                }
                mock_transcript.return_value = "Never gonna give you up..."
                mock_judge.return_value = {
                    "authentic": False,
                    "reasoning": "Content does not match goal description.",
                }

                from app.workers.youtube import run_youtube_verification

                criteria_data = {
                    "min_duration_seconds": 120,
                    "video_description": "A walkthrough demo showing how the sacrifice app works",
                }
                # A failed verification dispatches the pledge charge; isolate
                # billing (as the deadline-worker tests do) and assert dispatch.
                with patch(
                    "app.workers.payments.process_charge_for_goal",
                    new_callable=AsyncMock,
                ) as mock_charge:
                    await run_youtube_verification(
                        goal_id=goal.id,
                        submission_id=submission.id,
                        proof_data=submission.proof_data,
                        criteria_data=criteria_data,
                        db=db,
                    )
                mock_charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))

            await db.refresh(goal)
            await db.refresh(submission)
            assert goal.status == "failed"
            assert submission.verification_status == "failed"

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    body = status_resp.json()
    assert body["verification_status"] == "failed"
    await local_engine.dispose()
