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


async def test_request_new_goal_type_unauthenticated_returns_401():
    async with make_client() as client:
        response = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            json={"prompt_summary": "I want to track water consumption"},
        )
    assert response.status_code == 401


async def test_request_new_goal_type_nonexistent_session_returns_404():
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "I want to track water consumption"},
        )
    assert response.status_code == 404


async def test_request_new_goal_type_returns_501():
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "I want to track water consumption"},
        )
    # Pre-impl: this will be 404 because the route doesn't exist yet (FastAPI
    # returns 404 for unknown routes) or because chat_sessions table doesn't
    # exist. Both are valid "red" outcomes. The Dev will wire it to 501.
    assert response.status_code == 501
    body = response.json()
    assert body["detail"] == "Goal-type generation is delivered in D010"