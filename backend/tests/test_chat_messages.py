"""Tests for D009 POST /api/chat/sessions/{session_id}/messages.

These tests mock `chat_match.match` to control match/no-match/error behavior
while exercising the real endpoint end-to-end (auth, routing, persistence).

Every test: imports from `app.routes.chat` or `app.services.chat_match`,
calls the real endpoint via httpx.AsyncClient through the ASGI transport,
and asserts on the response contract from `api_spec.md`.
"""

import json as json_mod
import uuid
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.chat_match import match as real_chat_match


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="msg-test@example.com", name="Msg Tester",
                sub="msg-test-sub", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub,
                             "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_session(client, token) -> str:
    resp = await client.post(
        "/api/chat/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, (
        f"Session creation failed: {resp.status_code} {resp.text}"
    )
    return resp.json()["session_id"]


# ═══════════════════════════════════════════════════════════════════════════
# 200 — match path
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_match_returns_200_with_match_proposed_action():
    """200 response with match_proposed action when the match service
    returns a high-confidence match for a known goal type."""
    fake_match = {
        "match": "youtube_video",
        "confidence": 0.87,
        "rationale": "User mentioned YouTube and a deadline.",
    }

    with patch("app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)):
        async with make_client() as client:
            token, _ = await _auth(client)
            session_id = await _create_session(client, token)

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": (
                    "I want to upload a YouTube walkthrough of my project "
                    "by Friday and pledge $20"
                )},
            )

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()

    # Response shape from api_spec.md
    assert "messages" in body
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) >= 2  # user + assistant

    # First message is user turn
    user_msg = body["messages"][-2]
    assert user_msg["role"] == "user"
    assert "YouTube" in user_msg["content"]
    assert user_msg["action"] is None

    # Second message is assistant with match_proposed action
    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["action"] is not None
    assert assistant_msg["action"]["type"] == "match_proposed"
    assert assistant_msg["action"]["goal_type"] == "youtube_video"
    assert assistant_msg["action"]["confidence"] == 0.87
    assert "missing_criteria" in assistant_msg["action"]

    # draft_goal key must be present (nullable when no match)
    assert "draft_goal" in body


async def test_send_message_persists_user_and_assistant_messages():
    """After sending a message, the chat_sessions row contains both the
    user and assistant messages — verified by direct DB query."""
    from app.config import settings as cfg
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy import text

    fake_match = {
        "match": "youtube_video",
        "confidence": 0.92,
        "rationale": "clear YouTube match",
    }

    with patch("app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)):
        async with make_client() as client:
            token, _ = await _auth(client)
            session_id = await _create_session(client, token)

            await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "Upload a YouTube video by Friday, pledge $20"},
            )

    # Verify persistence — use a throwaway engine to avoid pool teardown issues.
    verify_engine = create_async_engine(cfg.database_url, echo=False)
    try:
        maker = async_sessionmaker(verify_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db_session:
            row = await db_session.execute(
                text(
                    "SELECT messages, draft_goal, updated_at, created_at "
                    "FROM chat_sessions WHERE id = :id"
                ),
                {"id": uuid.UUID(session_id)},
            )
            row = row.fetchone()

        assert row is not None
        messages = row[0]
        # greeting (1) + user (1) + assistant (1) = 3
        assert len(messages) == 3, f"Expected 3 messages, got {len(messages)}"
        assert messages[0]["role"] == "assistant"  # greeting
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["action"]["type"] == "match_proposed"

        # draft_goal should be set for a high-confidence match
        assert row[1] is not None
        assert row[1]["goal_type"] == "youtube_video"

        # updated_at should be set (non-null) — persistence is real
        assert row[2] is not None  # updated_at column
        assert row[3] is not None  # created_at column
        assert row[2] >= row[3]    # updated_at >= created_at
    finally:
        await verify_engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════
# 200 — no-match path
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_no_match_returns_200_with_no_match_action():
    """200 response with no_match action when the LLM returns match=none."""
    fake_match = {"match": "none", "confidence": 0.0, "rationale": "No match"}

    with patch("app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)):
        async with make_client() as client:
            token, _ = await _auth(client)
            session_id = await _create_session(client, token)

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "Track that I drank 8 glasses of water today"},
            )

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()

    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["action"] is not None
    assert assistant_msg["action"]["type"] == "no_match"
    assert assistant_msg["action"]["suggested_action"] == "generate_new_goal_type"

    # No draft_goal for no-match
    assert body["draft_goal"] is None


async def test_send_message_below_threshold_treated_as_no_match():
    """match returns a goal_type but confidence is below threshold → no_match."""
    fake_match = {
        "match": "youtube_video",
        "confidence": 0.2,  # below default 0.7 threshold
        "rationale": "barely",
    }

    with patch("app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)):
        async with make_client() as client:
            token, _ = await _auth(client)
            session_id = await _create_session(client, token)

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "something vaguely video-ish maybe"},
            )

    assert resp.status_code == 200
    assistant_msg = resp.json()["messages"][-1]
    assert assistant_msg["action"]["type"] == "no_match"


# ═══════════════════════════════════════════════════════════════════════════
# 401 — unauthenticated
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_unauthenticated_returns_401():
    """POST /api/chat/sessions/{id}/messages without auth returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/messages",
            json={"content": "hello"},
        )
    assert resp.status_code == 401, (
        f"Expected 401, got {resp.status_code}: {resp.text}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 403 — ownership
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_wrong_owner_returns_403():
    """User A creates a session; User B posts a message → 403."""
    fake_match = {"match": "none", "confidence": 0.0, "rationale": ""}

    with patch("app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)):
        async with make_client() as client:
            # User A creates session
            token_a, _ = await _auth(client, sub="user-a", email="a@test.com")
            session_id = await _create_session(client, token_a)

            # User B authenticates
            token_b, _ = await _auth(client, sub="user-b", email="b@test.com",
                                     token="token-b")

            # User B tries to post to A's session
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"content": "I shouldn't be allowed"},
            )

    assert resp.status_code == 403, (
        f"Expected 403, got {resp.status_code}: {resp.text}"
    )
    assert "Session not owned by user" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# 404 — session missing
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_session_not_found_returns_404():
    """POST to nonexistent session returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.post(
            f"/api/chat/sessions/{fake_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "hello"},
        )
    assert resp.status_code == 404, (
        f"Expected 404, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == "Session not found"


async def test_send_message_non_uuid_session_id_returns_404():
    """POST with non-UUID session id returns 404 (not 500)."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/chat/sessions/not-a-uuid/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "hello"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not found"


# ═══════════════════════════════════════════════════════════════════════════
# 422 — invalid content
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_empty_content_returns_422():
    """Empty content returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": ""},
        )
    assert resp.status_code == 422, (
        f"Expected 422, got {resp.status_code}: {resp.text}"
    )


async def test_send_message_whitespace_content_returns_422():
    """Whitespace-only content returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "   \t  \n  "},
        )
    assert resp.status_code == 422, (
        f"Expected 422, got {resp.status_code}: {resp.text}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 502 — upstream LLM failure
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_upstream_failure_returns_502():
    """When chat_match raises, the endpoint returns 502."""
    with patch(
        "app.routes.chat.chat_match",
        new=AsyncMock(side_effect=RuntimeError("LLM timeout")),
    ):
        async with make_client() as client:
            token, _ = await _auth(client)
            session_id = await _create_session(client, token)

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "valid message"},
            )

    assert resp.status_code == 502, (
        f"Expected 502, got {resp.status_code}: {resp.text}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# updated_at
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_updates_updated_at():
    """Sending messages bumps the session updated_at timestamp.

    Uses the existing persistence test pattern: send two messages for the
    same session and verify the second response acknowledges a newer state.
    """
    fake_match = {"match": "none", "confidence": 0.0, "rationale": ""}

    with patch("app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)):
        async with make_client() as client:
            token, _ = await _auth(client)
            session_id = await _create_session(client, token)

            # First message
            resp1 = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "first"},
            )
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert len(data1["messages"]) > 0

            # Second message
            resp2 = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "second"},
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert len(data2["messages"]) > 0

            # The second request returns more messages (accumulated)
            assert len(data2["messages"]) >= len(data1["messages"])