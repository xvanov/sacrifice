"""
Tests that proof-submission dispatch uses the goal-type registry rather than
hard-coded ``if/elif`` branching on ``goal_type`` in ``routes/goals.py``.

These tests verify the registry integration point: they mock the registry
get_type/verify and assert that the route calls through the registry,
NOT that the route internally branches on the goal_type string.
"""

from unittest.mock import AsyncMock, patch

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


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


async def _create_active_goal(client, token, goal_type, criteria):
    """Create an active goal and return its ID."""
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": f"Test {goal_type} goal",
            "description": "Dispatch test",
            "deadline": "2026-12-31T00:00:00Z",
            "pledge_amount": 1000,
            "goal_type": goal_type,
            "criteria": criteria,
        },
    )
    assert resp.status_code in (200, 201), f"Goal creation failed: {resp.text}"
    goal_id = resp.json()["id"]

    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    return goal_id


class TestProofSubmissionUsesRegistry:
    """Verify that proof submission dispatches through the registry."""

    @pytest.mark.parametrize(
        "goal_type, proof_body",
        [
            (
                "youtube_video",
                {"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            ),
            ("api_endpoint", {"url": "https://httpbin.org/get", "method": "GET"}),
            (
                "dev_sandbox",
                {
                    "repo_url": "https://github.com/test/repo",
                    "test_command": "make test",
                },
            ),
            ("github_repo", {"repo_url": "https://github.com/test/repo"}),
        ],
    )
    async def test_submit_proof_calls_registry_get_type_for_each_type(
        self,
        goal_type,
        proof_body,
    ):
        """The route resolves the goal type via the registry and dispatches
        proof through the plugin's submit_proof() + async verification.

        (Loop-4 contract: validation/extraction happens in submit_proof; the
        verifier runs asynchronously, so the route no longer awaits verify().)
        """
        from unittest.mock import MagicMock

        mock_goal_type = MagicMock()
        # submit_proof validates + returns the canonical proof/criteria dicts.
        mock_goal_type.submit_proof.return_value = {
            "proof_data": {"ok": True},
            "criteria_data": {},
        }
        mock_goal_type.name = goal_type

        criteria_map = {
            "youtube_video": {"min_duration_seconds": 60, "video_description": "test"},
            "api_endpoint": {
                "url": "https://httpbin.org/get",
                "method": "GET",
                "expected_status": 200,
            },
            "dev_sandbox": {
                "repo_url": "https://github.com/test/repo",
                "test_command": "make test",
            },
            "github_repo": {
                "repo_url": "https://github.com/test/repo",
                "conditions": [],
            },
        }

        with patch(
            "app.goal_types.registry.get_type",
            return_value=mock_goal_type,
        ) as mock_get_type:
            async with make_client() as client:
                token, _ = await _auth(client)
                goal_id = await _create_active_goal(
                    client,
                    token,
                    goal_type,
                    criteria_map[goal_type],
                )
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    json=proof_body,
                )

            mock_get_type.assert_called_with(goal_type)
            mock_goal_type.submit_proof.assert_called_once()
            mock_goal_type.dispatch_verification.assert_called_once()

        assert resp.status_code == 202

    async def test_submit_proof_validation_error_returns_422(self):
        """A proof the plugin rejects as malformed surfaces as 422, not 202.

        (Replaces the old 'verifier rejected' path: validation now happens up
        front in submit_proof(), so bad proof never creates a pending row.)
        """
        from unittest.mock import MagicMock

        from app.goal_types.base import ProofValidationError

        mock_goal_type = MagicMock()
        mock_goal_type.submit_proof.side_effect = ProofValidationError("bad proof")
        mock_goal_type.name = "youtube_video"

        with patch(
            "app.goal_types.registry.get_type",
            return_value=mock_goal_type,
        ):
            async with make_client() as client:
                token, _ = await _auth(client)
                goal_id = await _create_active_goal(
                    client,
                    token,
                    "youtube_video",
                    {"min_duration_seconds": 60, "video_description": "test"},
                )
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                )

        assert resp.status_code == 422
