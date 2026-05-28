"""Tests for chat generation endpoints.

These tests assert on endpoints that do NOT exist yet. Every test in this
file MUST fail (RED) on first run against the current codebase.

Covers:
- POST /api/chat/sessions/{session_id}/request-new-goal-type
- GET /api/chat/sessions/{session_id}/generation-status
- POST /api/chat/sessions/{session_id}/accept-generated-type
- POST /api/chat/sessions/{session_id}/iterate-generated-type
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


VALID_GOAL = {
    "title": "20 morning pushups",
    "description": "Do 20 pushups every morning at 7am, verified with my phone camera.",
    "pledge_amount": 1000,
    "currency": "usd",
    "deadline": "2026-05-26T11:00:00Z",
    "timezone": "America/New_York",
    "charity_id": "acct_charity123",
    "recurrence": "daily",
}


def _make_session_id():
    return str(uuid.uuid4())


# ─── POST /api/chat/sessions/{session_id}/request-new-goal-type ──────


async def test_request_new_goal_type_returns_202_on_success():
    """request-new-goal-type must return 202 with direction_id and goal_id."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.synthesize_and_create_goal", new_callable=AsyncMock
        ) as mock_synth:
            mock_synth.return_value = {
                "direction_id": "011-pushup-counter",
                "goal_id": str(uuid.uuid4()),
                "status": "queued",
            }

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                    "goal_payload_draft": VALID_GOAL,
                },
            )

        assert resp.status_code == 202
        body = resp.json()
        assert "direction_id" in body
        assert "goal_id" in body
        assert body["status"] == "queued"


async def test_request_new_goal_type_returns_401_without_auth():
    """request-new-goal-type must reject unauthenticated requests."""
    session_id = _make_session_id()

    async with make_client() as client:
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            json={
                "prompt_summary": "Do 20 pushups",
                "goal_payload_draft": VALID_GOAL,
            },
        )

    assert resp.status_code == 401


async def test_request_new_goal_type_returns_409_when_generation_in_flight():
    """request-new-goal-type must return 409 when user has active generation."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.synthesize_and_create_goal", new_callable=AsyncMock
        ) as mock_synth:
            mock_synth.side_effect = ValueError(
                "conflict:generation_in_flight:011-existing-type"
            )

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "Do 20 pushups every morning",
                    "goal_payload_draft": VALID_GOAL,
                },
            )

        assert resp.status_code == 409
        body = resp.json()
        assert "direction_id" in body


async def test_request_new_goal_type_returns_422_for_vague_prompt():
    """request-new-goal-type must return 422 when prompt is too vague."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.synthesize_and_create_goal", new_callable=AsyncMock
        ) as mock_synth:
            mock_synth.side_effect = ValueError("prompt:too_vague")

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "do something",
                    "goal_payload_draft": VALID_GOAL,
                },
            )

        assert resp.status_code == 422


async def test_request_new_goal_type_returns_429_when_spend_cap_hit():
    """request-new-goal-type must return 429 when daily AI budget exceeded."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.synthesize_and_create_goal", new_callable=AsyncMock
        ) as mock_synth:
            mock_synth.side_effect = ValueError("spend_cap:exceeded")

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "Do 20 pushups every morning",
                    "goal_payload_draft": VALID_GOAL,
                },
            )

        assert resp.status_code == 429
        body = resp.json()
        assert "budget" in body.get("detail", "").lower() or "budget" in str(body).lower()


# ─── GET /api/chat/sessions/{session_id}/generation-status ────────────


async def test_generation_status_returns_200_with_status_fields():
    """generation-status must return status, direction_id, and optional pr_url."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.get_generation_status_for_session",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {
                "direction_id": "011-pushup-counter",
                "status": "pr_open",
                "pr_url": "https://github.com/xvanov/sacrifice/pull/47",
                "summary": "Dev iterating on tests.",
            }

            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["direction_id"] == "011-pushup-counter"
        assert body["status"] == "pr_open"
        assert "pr_url" in body


async def test_generation_status_returns_404_when_no_generation():
    """generation-status must return 404 when session has no generation."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.get_generation_status_for_session",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.side_effect = ValueError("generation:not_found")

            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 404


async def test_generation_status_all_lifecycle_states_are_valid():
    """generation-status must return only valid lifecycle states."""
    valid_states = {"queued", "in_progress", "pr_open", "pr_merged", "rejected"}

    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.get_generation_status_for_session",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {
                "direction_id": "011-pushup-counter",
                "status": "pr_merged",
                "pr_url": "https://github.com/xvanov/sacrifice/pull/47",
            }

            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )

        body = resp.json()
        assert body["status"] in valid_states


# ─── POST /api/chat/sessions/{session_id}/accept-generated-type ───────


async def test_accept_generated_type_returns_200_on_success():
    """accept-generated-type must return 200 with goal_id and active status."""
    session_id = _make_session_id()
    goal_id = str(uuid.uuid4())

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.accept_generated_type_for_session",
            new_callable=AsyncMock,
        ) as mock_accept:
            mock_accept.return_value = {"goal_id": goal_id, "status": "active"}

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"


async def test_accept_generated_type_returns_409_when_not_merged():
    """accept-generated-type must return 409 when generation not yet merged."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.accept_generated_type_for_session",
            new_callable=AsyncMock,
        ) as mock_accept:
            mock_accept.side_effect = ValueError("generation:not_merged")

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 409


# ─── POST /api/chat/sessions/{session_id}/iterate-generated-type ──────


async def test_iterate_generated_type_returns_202_on_success():
    """iterate-generated-type must return 202 with new and previous direction ids."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.iterate_generated_type_for_session",
            new_callable=AsyncMock,
        ) as mock_iterate:
            mock_iterate.return_value = {
                "direction_id": "047-pushup-counter-side-angle",
                "previous_direction_id": "011-pushup-counter",
                "status": "queued",
            }

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/iterate-generated-type",
                headers={"Authorization": f"Bearer {token}"},
                json={"feedback": "Use a side-on camera angle; count partial reps as 0.5."},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert "direction_id" in body
        assert "previous_direction_id" in body
        assert body["previous_direction_id"] == "011-pushup-counter"
        assert body["status"] == "queued"


async def test_iterate_generated_type_rejects_empty_feedback():
    """iterate-generated-type must return 422 for empty/whitespace feedback."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "   "},
        )

        assert resp.status_code == 422


async def test_iterate_generated_type_returns_409_when_already_accepted():
    """iterate-generated-type must return 409 if goal already accepted."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.iterate_generated_type_for_session",
            new_callable=AsyncMock,
        ) as mock_iterate:
            mock_iterate.side_effect = ValueError("goal:already_accepted")

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/iterate-generated-type",
                headers={"Authorization": f"Bearer {token}"},
                json={"feedback": "Change the camera angle"},
            )

        assert resp.status_code == 409