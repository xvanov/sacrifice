"""
Tests for the ``GET /api/goal-types`` endpoint.

Covers:
- Authenticated success (200) with correct response shape
- Unauthenticated access (401)
- Response matches the api_spec.md contract
"""

from unittest.mock import patch

from app.main import app
from httpx import ASGITransport, AsyncClient


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


class TestGoalTypesEndpoint:
    async def test_get_goal_types_returns_200_for_authenticated_user(self):
        """Authenticated users receive 200 with a goal_types list."""
        async with make_client() as client:
            token, _ = await _auth(client)
            resp = await client.get(
                "/api/goal-types",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200

    async def test_get_goal_types_returns_401_without_token(self):
        """No token → 401 Unauthorized."""
        async with make_client() as client:
            resp = await client.get("/api/goal-types")
        assert resp.status_code == 401

    async def test_get_goal_types_returns_401_with_invalid_token(self):
        """An invalid/expired token → 401 Unauthorized."""
        async with make_client() as client:
            resp = await client.get(
                "/api/goal-types",
                headers={"Authorization": "Bearer invalid.token.here"},
            )
        assert resp.status_code == 401

    async def test_get_goal_types_response_shape(self):
        """Response matches: {"goal_types": [{name, description, sample_prompts, criteria_schema}, ...]}."""
        async with make_client() as client:
            token, _ = await _auth(client)
            resp = await client.get(
                "/api/goal-types",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "goal_types" in body
        assert isinstance(body["goal_types"], list)

        for gt in body["goal_types"]:
            assert "name" in gt
            assert isinstance(gt["name"], str)
            assert "description" in gt
            assert isinstance(gt["description"], str)
            assert "sample_prompts" in gt
            assert isinstance(gt["sample_prompts"], list)
            assert "criteria_schema" in gt
            assert isinstance(gt["criteria_schema"], dict)

    async def test_get_goal_types_includes_all_four_core_types(self):
        """The four ported types must appear in the response."""
        async with make_client() as client:
            token, _ = await _auth(client)
            resp = await client.get(
                "/api/goal-types",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        names = {gt["name"] for gt in resp.json()["goal_types"]}
        assert "youtube_video" in names
        assert "api_endpoint" in names
        assert "dev_sandbox" in names
        assert "github_repo" in names
