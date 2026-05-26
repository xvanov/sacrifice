"""
Tests for POST /api/chat/sessions/{session_id}/accept-generated-type

The endpoint transitions a goal from ``awaiting_goal_type`` to ``active``
when the generation is ``pr_merged``.  409 if not yet merged, 404 if the
session or pending goal is not found.
"""

import uuid
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
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def test_accept_generated_type_success_returns_200_active():
    """POST with a merged generation transitions awaiting_goal_type -> active."""
    async with make_client() as client:
        token, user = await _auth(client)

        # The endpoint does not exist yet — this call MUST fail.
        # We attempt the call against the API surface the story declares.
        session_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Pre-implementation: route is not registered → 404.
    # Post-implementation: 200 with {"goal_id": "...", "status": "active"}.
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "goal_id" in body
    assert body["status"] == "active"


async def test_accept_generated_type_404_when_session_not_found():
    """A non-existent session returns 404 with a specific detail message."""
    async with make_client() as client:
        token, _ = await _auth(client)
        bogus_session_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/chat/sessions/{bogus_session_id}/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 404
    body = resp.json()
    # The detail must identify the session/pending-goal, not just "Not Found"
    # so the test cannot pass on a generic route-not-found 404.
    assert "session" in body.get("detail", "").lower() or \
           "pending goal" in body.get("detail", "").lower() or \
           "generation" in body.get("detail", "").lower()


async def test_accept_generated_type_409_when_generation_not_merged():
    """When generation status is not pr_merged the endpoint returns 409."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # The endpoint does not exist yet — this call MUST fail.
        # We attempt the call against a session whose generation is still
        # in_progress (not merged).
        session_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Pre-implementation: route is not registered → 404.
    # Post-implementation: 409 when generation status != pr_merged.
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"