"""Tests for POST /api/chat/sessions/{session_id}/messages."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.chat_match import MatchResult

GREETING_MESSAGE = {
    "role": "assistant",
    "content": "Tell me what you want to do, and I'll figure out how to track it.",
    "action": None,
}


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


async def _create_session(client, token):
    resp = await client.post(
        "/api/chat/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    return resp.json()["session_id"]


# ── 200 match response ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_returns_200_with_match_proposed_action():
    """On a matching prompt the endpoint returns 200 with a match_proposed
    action, draft_goal populated, and missing_criteria sourced from the
    registry's criteria_schema."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        with patch(
            "app.routes.chat.match_message",
            new_callable=AsyncMock,
        ) as mock_match:
            mock_match.return_value = MatchResult(
                matched=True,
                goal_type="youtube_video",
                confidence=0.87,
                rationale="User wants to upload a video",
            )

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "I want to upload a YouTube walkthrough by Friday"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()

    assert isinstance(body["messages"], list)
    assert len(body["messages"]) >= 3  # greeting + user + assistant

    # User message should be persisted
    user_msg = body["messages"][-2]
    assert user_msg["role"] == "user"
    assert "YouTube walkthrough" in user_msg["content"]
    assert user_msg["action"] is None

    # Assistant message with match_proposed action
    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["action"]["type"] == "match_proposed"
    assert assistant_msg["action"]["goal_type"] == "youtube_video"
    assert assistant_msg["action"]["confidence"] == 0.87
    assert isinstance(assistant_msg["action"]["missing_criteria"], list)

    # missing_criteria must include required type-specific fields
    # youtube_video's criteria_schema.required = ["min_duration_seconds", "video_description"]
    assert "min_duration_seconds" in assistant_msg["action"]["missing_criteria"]
    assert "video_description" in assistant_msg["action"]["missing_criteria"]

    # draft_goal must be present and include goal_type
    assert body["draft_goal"] is not None
    assert body["draft_goal"]["goal_type"] == "youtube_video"

    # match_message called exactly once with user message + context
    mock_match.assert_called_once()
    call_args = mock_match.call_args
    assert call_args[0][0] == "I want to upload a YouTube walkthrough by Friday"


# ── 200 no-match response ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_returns_200_with_no_match_action():
    """When the match confidence is below threshold the endpoint returns
    a no_match action with suggested_action=generate_new_goal_type."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        with patch(
            "app.routes.chat.match_message",
            new_callable=AsyncMock,
        ) as mock_match:
            mock_match.return_value = MatchResult(
                matched=False,
                goal_type=None,
                confidence=0.2,
                rationale="No matching goal type",
            )

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Track that I drank 8 glasses of water today"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()

    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["action"]["type"] == "no_match"
    assert assistant_msg["action"]["suggested_action"] == "generate_new_goal_type"
    assert "built-in" in assistant_msg["content"]


# ── 401 unauthenticated ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_returns_401_unauthenticated():
    """Posting a message without auth returns 401."""
    async with make_client() as client:
        resp = await client.post(
            f"/api/chat/sessions/{uuid.uuid4()}/messages",
            json={"content": "hello"},
        )
    assert resp.status_code == 401


# ── 403 ownership ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_returns_403_for_wrong_owner():
    """Posting a message to another user's session returns 403."""
    async with make_client() as client:
        token_a, _ = await _auth(client)
        session_id = await _create_session(client, token_a)

        # Auth as a different user
        token_b, _ = await _auth(
            client, email="other@example.com", name="Other", sub="other-sub"
        )
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={"content": "hello from other user"},
            headers={"Authorization": f"Bearer {token_b}"},
        )

    assert resp.status_code == 403
    assert "not owned" in resp.json()["detail"]


# ── 404 session missing ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_returns_404_for_missing_session():
    """Posting to a nonexistent session returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            f"/api/chat/sessions/{uuid.uuid4()}/messages",
            json={"content": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
    assert "Session not found" in resp.json()["detail"]


# ── 422 whitespace input ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_returns_422_for_whitespace_content():
    """Empty or whitespace-only content returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        for bad_content in ("", "   ", "\n\t  "):
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": bad_content},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 422, f"expected 422 for {bad_content!r}"


# ── 502 upstream failure ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_returns_502_on_upstream_failure():
    """When the LLM call raises ChatMatchError, the endpoint returns 502
    with messages persisted and an assistant retry action."""
    from app.services.chat_match import ChatMatchError

    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        with patch(
            "app.routes.chat.match_message",
            new_callable=AsyncMock,
        ) as mock_match:
            mock_match.side_effect = ChatMatchError("simulated failure")

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "something"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 502
    body = resp.json()

    # User message + retry assistant message both persisted
    assert len(body["messages"]) >= 3
    user_msg = body["messages"][-2]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "something"

    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["action"]["type"] == "retry"
    assert "try again" in assistant_msg["content"]


# ── Persistence test ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_persists_user_and_assistant_messages():
    """Messages sent to the endpoint are persisted and returned in
    subsequent requests."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        with patch(
            "app.routes.chat.match_message",
            new_callable=AsyncMock,
        ) as mock_match:
            mock_match.return_value = MatchResult(
                matched=True,
                goal_type="youtube_video",
                confidence=0.9,
                rationale="match",
            )

            await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "first message"},
                headers={"Authorization": f"Bearer {token}"},
            )

            mock_match.return_value = MatchResult(
                matched=False,
                goal_type=None,
                confidence=0.1,
                rationale="no match",
            )

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "second message"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()

    # Should have: greeting + user1 + assistant1 + user2 + assistant2
    assert len(body["messages"]) == 5, (
        f"expected 5 messages (greeting + 2 user + 2 assistant), got {len(body['messages'])}"
    )

    roles = [m["role"] for m in body["messages"]]
    assert roles == ["assistant", "user", "assistant", "user", "assistant"]

    assert body["messages"][1]["content"] == "first message"
    assert body["messages"][3]["content"] == "second message"

    # First assistant has match_proposed
    assert body["messages"][2]["action"]["type"] == "match_proposed"
    # Second assistant has no_match
    assert body["messages"][4]["action"]["type"] == "no_match"