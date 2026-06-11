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

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email, name, sub, token):
    """Authenticate a unique test identity — no default params to prevent
    cross-test coupling via shared identities."""
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


# Per-test identity helper — call with a unique suffix per test
_ID_COUNTER = 0


def _uniq_id() -> int:
    global _ID_COUNTER
    _ID_COUNTER += 1
    return _ID_COUNTER


async def _auth_uniq(client):
    """Shortcut: auth with a globally unique identity."""
    n = _uniq_id()
    return await _auth(
        client,
        email=f"test{n}@example.com",
        name=f"Tester{n}",
        sub=f"test-sub-{n}",
        token=f"valid-token-{n}",
    )


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
            token, _ = await _auth_uniq(client)
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

    # ── api_spec.md contract shape ──
    assert "messages" in body
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) >= 2  # user + assistant

    # User turn
    user_msg = body["messages"][-2]
    assert user_msg["role"] == "user"
    assert "YouTube" in user_msg["content"]
    assert user_msg["action"] is None

    # Assistant match_proposed action per api_spec.md
    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    action = assistant_msg["action"]
    assert action is not None
    assert action["type"] == "match_proposed"
    assert action["goal_type"] == "youtube_video"
    assert action["confidence"] == 0.87

    # missing_criteria is a non-empty list of required fields the chat must
    # still collect conversationally (contract: both top-level and nested).
    assert isinstance(action["missing_criteria"], list)
    assert len(action["missing_criteria"]) > 0, (
        "missing_criteria must not be empty for a match with partial extraction"
    )
    # Top-level fields not in the prompt must be missing
    assert "deadline" in action["missing_criteria"], (
        f"deadline should be missing; got {action['missing_criteria']}"
    )
    assert "charity_id" in action["missing_criteria"], (
        f"charity_id should be missing; got {action['missing_criteria']}"
    )

    # draft_goal per api_spec.md contract
    assert "draft_goal" in body
    assert body["draft_goal"] is not None
    assert body["draft_goal"]["goal_type"] == "youtube_video"
    # pledge_amount must be in cents
    assert body["draft_goal"]["pledge_amount"] == 2000, (
        f"pledge_amount must be in cents (2000 for $20), "
        f"got {body['draft_goal'].get('pledge_amount')}"
    )


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
            token, _ = await _auth_uniq(client)
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
            token, _ = await _auth_uniq(client)
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
    """match returns a goal_type but confidence is below threshold → no_match.

    Asserts the full no_match action shape, assistant content, draft_goal
    absence, and persistence of both user and assistant messages per the
    api_spec.md contract.
    """
    from app.config import settings as cfg
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy import text

    fake_match = {
        "match": "youtube_video",
        "confidence": 0.2,  # below default 0.7 threshold
        "rationale": "barely",
    }

    with patch("app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)):
        async with make_client() as client:
            token, _ = await _auth_uniq(client)
            session_id = await _create_session(client, token)

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "something vaguely video-ish maybe"},
            )

    assert resp.status_code == 200
    body = resp.json()

    # Structured no_match action shape from api_spec.md
    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["action"] is not None
    assert assistant_msg["action"]["type"] == "no_match"
    assert assistant_msg["action"]["suggested_action"] == "generate_new_goal_type"
    assert "built-in" in assistant_msg["content"].lower()

    # No draft_goal for no-match
    assert body["draft_goal"] is None

    # Verify both user and assistant messages are persisted
    verify_engine = create_async_engine(cfg.database_url, echo=False)
    try:
        maker = async_sessionmaker(verify_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db_session:
            row = await db_session.execute(
                text("SELECT messages, draft_goal FROM chat_sessions WHERE id = :id"),
                {"id": uuid.UUID(session_id)},
            )
            row = row.fetchone()

        assert row is not None
        messages = row[0]
        # greeting + user + assistant = 3
        assert len(messages) == 3, f"Expected 3 messages, got {len(messages)}"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["action"]["type"] == "no_match"
        assert row[1] is None  # draft_goal is null
    finally:
        await verify_engine.dispose()


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
            # User A creates session — unique identity
            token_a, _ = await _auth_uniq(client)
            session_id = await _create_session(client, token_a)

            # User B authenticates — different unique identity
            token_b, _ = await _auth_uniq(client)

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
        token, _ = await _auth_uniq(client)
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
        token, _ = await _auth_uniq(client)
        resp = await client.post(
            "/api/chat/sessions/not-a-uuid/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "hello"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not found"


# ═══════════════════════════════════════════════════════════════════════════
# 422 — invalid content (empty / whitespace-only)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_content", [
    "",
    "   \t  \n  ",
])
async def test_send_message_invalid_content_returns_422(bad_content):
    """Empty or whitespace-only content returns 422."""
    async with make_client() as client:
        token, _ = await _auth_uniq(client)
        session_id = await _create_session(client, token)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": bad_content},
        )
    assert resp.status_code == 422, (
        f"Expected 422, got {resp.status_code}: {resp.text}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 502 — upstream LLM failure
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_upstream_failure_returns_502():
    """When chat_match raises, the endpoint returns 502 per api_spec.md.

    The user message AND a retry-friendly assistant message are persisted
    so the frontend retry card flow (flow.md) works when the client reloads
    the session after a transient failure.
    """
    from app.config import settings as cfg
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy import text

    with patch(
        "app.routes.chat.chat_match",
        new=AsyncMock(side_effect=RuntimeError("LLM timeout")),
    ):
        async with make_client() as client:
            token, _ = await _auth_uniq(client)
            session_id = await _create_session(client, token)

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "valid message"},
            )

    assert resp.status_code == 502, (
        f"Expected 502, got {resp.status_code}: {resp.text}"
    )
    assert "detail" in resp.json()

    # Verify both user message and retry-friendly assistant message persisted
    verify_engine = create_async_engine(cfg.database_url, echo=False)
    try:
        maker = async_sessionmaker(verify_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db_session:
            row = await db_session.execute(
                text("SELECT messages FROM chat_sessions WHERE id = :id"),
                {"id": uuid.UUID(session_id)},
            )
            row = row.fetchone()

        assert row is not None
        messages = row[0]
        # greeting + user + assistant retry = 3
        assert len(messages) == 3, (
            f"Expected 3 messages (greeting + user + retry assistant), got {len(messages)}"
        )
        assert messages[0]["role"] == "assistant"  # greeting
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "valid message"
        # Retry-friendly assistant message per flow.md
        assert messages[2]["role"] == "assistant"
        assert "try again" in messages[2]["content"].lower(), (
            f"Retry message should prompt retry; got: {messages[2]['content']}"
        )
    finally:
        await verify_engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════
# chat_match invocation contract
# ═══════════════════════════════════════════════════════════════════════════


async def test_send_message_calls_chat_match_once_with_prior_context():
    """chat_match is called exactly once per accepted turn and receives ONLY
    the prior chat context — the current user message must NOT be in it."""
    fake_match = {"match": "none", "confidence": 0.0, "rationale": ""}

    async with make_client() as client:
        token, _ = await _auth_uniq(client)
        session_id = await _create_session(client, token)

        # ── First turn: context should be just the greeting ──
        with patch(
            "app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)
        ) as mock_match:
            resp1 = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "first message"},
            )
            assert resp1.status_code == 200

        assert mock_match.call_count == 1, (
            f"chat_match called {mock_match.call_count} times; expected exactly 1"
        )
        call_args, call_kwargs = mock_match.call_args
        context_arg = call_kwargs.get("chat_context", call_args[1] if len(call_args) > 1 else None)
        assert context_arg is not None, "chat_match must receive chat_context"

        # Current message must NOT be in prior context
        context_contents = {m.get("content", "") for m in context_arg}
        assert "first message" not in context_contents, (
            f"Current user message leaked into prior context: {context_arg}"
        )

        # Greeting must be the only message in context for first turn
        assert len(context_arg) == 1, (
            f"First turn context should be exactly 1 msg (greeting), got {len(context_arg)}"
        )
        assert context_arg[0]["role"] == "assistant"
        assert "Tell me what you want to do" in context_arg[0]["content"]

        # ── Second turn: context should include the first exchange only ──
        with patch(
            "app.routes.chat.chat_match", new=AsyncMock(return_value=fake_match)
        ) as mock_match2:
            resp2 = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": "second message"},
            )
            assert resp2.status_code == 200

        assert mock_match2.call_count == 1, (
            f"Second chat_match called {mock_match2.call_count} times; expected exactly 1"
        )
        call_args2, call_kwargs2 = mock_match2.call_args
        context_arg2 = call_kwargs2.get("chat_context", call_args2[1] if len(call_args2) > 1 else None)
        assert context_arg2 is not None, "chat_match must receive chat_context"

        # Current (second) message must NOT be in prior context
        context_contents2 = {m.get("content", "") for m in context_arg2}
        assert "second message" not in context_contents2, (
            f"Current user message leaked into prior context: {context_arg2}"
        )

        # Exact prior sequence: greeting, user1, assistant1 (the no_match response)
        assert len(context_arg2) == 3, (
            f"Second turn context should be exactly 3 msgs, got {len(context_arg2)}: {context_arg2}"
        )
        assert context_arg2[0]["role"] == "assistant"  # greeting
        assert "Tell me what you want to do" in context_arg2[0]["content"]
        assert context_arg2[1]["role"] == "user"
        assert context_arg2[1]["content"] == "first message"
        assert context_arg2[2]["role"] == "assistant"
        assert context_arg2[2]["action"]["type"] == "no_match"