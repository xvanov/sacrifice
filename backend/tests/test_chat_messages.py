"""Tests for POST /api/chat/sessions/{session_id}/messages."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.goal_types.registry import get_type as get_registry_type, list_types as list_registry_types
from app.main import app
from app.services.chat_match import ChatMatchError, MatchResult

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


async def _load_session_state(session_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT messages, draft_goal, updated_at "
                    "FROM chat_sessions WHERE id = :id"
                ),
                {"id": session_id},
            )
            return result.fetchone()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_send_message_returns_200_with_match_proposed_action():
    """A matched prompt persists extracted partial goal fields and reports
    missing criteria from the real create-goal contract."""
    prompt = (
        "I want to upload a YouTube walkthrough of my project by 2026-07-04 "
        "and pledge $20"
    )

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
                json={"content": prompt},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()

    assert len(body["messages"]) == 3
    assert body["messages"][0] == GREETING_MESSAGE
    assert body["messages"][-2] == {
        "role": "user",
        "content": prompt,
        "action": None,
    }

    assistant_msg = body["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["action"]["type"] == "match_proposed"
    assert assistant_msg["action"]["goal_type"] == "youtube_video"
    assert assistant_msg["action"]["confidence"] == 0.87
    assert set(assistant_msg["action"]["missing_criteria"]) == {
        "charity_id",
        "min_duration_seconds",
    }
    assert "criteria" not in assistant_msg["action"]["missing_criteria"]

    assert body["draft_goal"] == {
        "goal_type": "youtube_video",
        "title": "YouTube walkthrough of my project",
        "description": prompt,
        "pledge_amount": 2000,
        "currency": "usd",
        "deadline": body["draft_goal"]["deadline"],
        "criteria": {
            "video_description": "YouTube walkthrough of my project",
        },
    }
    assert body["draft_goal"]["deadline"].startswith("2026-07-04")

    mock_match.assert_awaited_once()
    assert mock_match.await_args.args == (prompt,)
    assert mock_match.await_args.kwargs["chat_context"] == [
        {"role": "assistant", "content": GREETING_MESSAGE["content"]}
    ]
    assert mock_match.await_args.kwargs["threshold"] == settings.chat_match_confidence_threshold

    catalog = mock_match.await_args.kwargs["catalog"]
    assert [entry.name for entry in catalog] == list_registry_types()
    youtube_catalog_entry = next(entry for entry in catalog if entry.name == "youtube_video")
    youtube_goal_type = get_registry_type("youtube_video")
    assert youtube_catalog_entry.description == youtube_goal_type.description
    assert youtube_catalog_entry.sample_prompts == youtube_goal_type.sample_prompts


@pytest.mark.asyncio
async def test_send_message_calls_chat_match_once_per_turn_with_prior_context_only():
    """Each accepted turn triggers exactly one matcher call and the second
    call receives only prior context, not the current user message."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        with patch(
            "app.routes.chat.match_message",
            new_callable=AsyncMock,
        ) as mock_match:
            mock_match.side_effect = [
                MatchResult(
                    matched=True,
                    goal_type="youtube_video",
                    confidence=0.9,
                    rationale="first match",
                ),
                MatchResult(
                    matched=False,
                    goal_type=None,
                    confidence=0.2,
                    rationale="second no match",
                ),
            ]

            first = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "first message"},
                headers={"Authorization": f"Bearer {token}"},
            )
            second = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "second message"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert mock_match.await_count == 2

    second_call = mock_match.await_args_list[1]
    assert second_call.args == ("second message",)
    assert second_call.kwargs["chat_context"][0] == {
        "role": "assistant",
        "content": GREETING_MESSAGE["content"],
    }
    assert second_call.kwargs["chat_context"][1] == {
        "role": "user",
        "content": "first message",
    }
    assert second_call.kwargs["chat_context"][2]["role"] == "assistant"
    assert "second message" not in [
        message["content"] for message in second_call.kwargs["chat_context"]
    ]



@pytest.mark.asyncio
async def test_send_message_match_confirmation_returns_awaiting_input_and_still_calls_match_once():
    """Pressing the matched-path confirmation should continue the server-backed
    conversation with an awaiting_input assistant prompt."""
    prompt = "I want to upload a YouTube walkthrough and pledge $20"
    confirmation = "Use this goal type: youtube_video"

    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        with patch(
            "app.routes.chat.match_message",
            new_callable=AsyncMock,
        ) as mock_match:
            mock_match.side_effect = [
                MatchResult(
                    matched=True,
                    goal_type="youtube_video",
                    confidence=0.91,
                    rationale="initial match",
                ),
                MatchResult(
                    matched=False,
                    goal_type=None,
                    confidence=0.12,
                    rationale="confirmation turn should use draft goal",
                ),
            ]

            first = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": prompt},
                headers={"Authorization": f"Bearer {token}"},
            )
            second = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": confirmation},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert first.status_code == 200
    assert second.status_code == 200

    body = second.json()
    assert body["messages"][-2] == {
        "role": "user",
        "content": confirmation,
        "action": None,
    }
    assert body["messages"][-1] == {
        "role": "assistant",
        "content": "What's your deadline?",
        "action": {
            "type": "awaiting_input",
            "field": "deadline",
            "prompt": "What's your deadline?",
        },
    }

    assert mock_match.await_count == 2
    assert mock_match.await_args_list[1].args == (confirmation,)


@pytest.mark.asyncio
async def test_send_message_returns_200_with_no_match_action():
    """When the matcher returns no match, the endpoint responds with the
    structured no_match affordance from the API spec."""
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
    assert assistant_msg["action"] == {
        "type": "no_match",
        "suggested_action": "generate_new_goal_type",
    }
    assert "built-in way to verify that yet" in assistant_msg["content"]
    mock_match.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_returns_401_unauthenticated():
    """Posting a message without auth returns 401."""
    async with make_client() as client:
        resp = await client.post(
            f"/api/chat/sessions/{uuid.uuid4()}/messages",
            json={"content": "hello"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_send_message_returns_403_for_wrong_owner():
    """Posting a message to another user's session returns 403."""
    async with make_client() as client:
        token_a, _ = await _auth(client)
        session_id = await _create_session(client, token_a)

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


@pytest.mark.asyncio
async def test_send_message_returns_502_on_upstream_failure_with_plain_retry_message():
    """Transient matcher failures return 502 and persist a plain assistant
    retry message with action null per the closed API action enum."""
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

    assert len(body["messages"]) == 3
    assert body["messages"][-2] == {
        "role": "user",
        "content": "something",
        "action": None,
    }
    assert body["messages"][-1]["role"] == "assistant"
    assert body["messages"][-1]["action"] is None
    assert "try again" in body["messages"][-1]["content"]

    stored = await _load_session_state(session_id)
    assert stored is not None
    assert stored.messages == body["messages"]
    assert stored.draft_goal is None


@pytest.mark.asyncio
async def test_send_message_persists_user_and_assistant_messages_and_updates_timestamp():
    """Accepted turns persist both messages, extracted draft fields, and bump
    updated_at on the stored session row."""
    prompt = "I want to upload a YouTube walkthrough by 2026-07-04 and pledge $20"

    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)
        before = await _load_session_state(session_id)

        await asyncio.sleep(0.01)

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

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": prompt},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    after = await _load_session_state(session_id)

    assert before is not None
    assert after is not None
    assert len(after.messages) == 3
    assert after.messages[1] == {"role": "user", "content": prompt, "action": None}
    assert after.messages[2]["role"] == "assistant"
    assert after.messages[2]["action"]["type"] == "match_proposed"
    assert after.draft_goal["goal_type"] == "youtube_video"
    assert after.draft_goal["pledge_amount"] == 2000
    assert after.draft_goal["currency"] == "usd"
    assert after.draft_goal["deadline"].startswith("2026-07-04")
    assert after.updated_at > before.updated_at
    mock_match.assert_awaited_once()
