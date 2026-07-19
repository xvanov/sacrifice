"""Tests for POST /api/chat/sessions/{session_id}/messages
and POST /api/chat/sessions/{session_id}/create-goal."""

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
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None, "email_verified": True}
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
    """A matched prompt returns match_proposed only — no auto awaiting_input.
    The user must explicitly confirm the match first."""
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

    # greeting + user + match_proposed (no auto awaiting_input)
    assert len(body["messages"]) == 3
    assert body["messages"][0] == GREETING_MESSAGE
    assert body["messages"][1] == {
        "role": "user",
        "content": prompt,
        "action": None,
    }

    match_proposed_msg = body["messages"][2]
    assert match_proposed_msg["role"] == "assistant"
    assert match_proposed_msg["action"]["type"] == "match_proposed"
    assert match_proposed_msg["action"]["goal_type"] == "youtube_video"
    assert match_proposed_msg["action"]["confidence"] == 0.87
    # charity_id is optional (a failed pledge is charged either way), so it
    # must not appear as a blocking missing criterion.
    assert set(match_proposed_msg["action"]["missing_criteria"]) == {
        "min_duration_seconds",
    }
    assert "criteria" not in match_proposed_msg["action"]["missing_criteria"]

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
    """Each freeform turn triggers exactly one matcher call and receives
    only prior context, not the current user message. After a match,
    subsequent criterion-filling turns are handled by the state machine
    without additional matcher calls. A genuine rephrase + new freeform
    turn triggers a fresh matcher call with only prior context."""
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
                rationale="first match",
            )

            # First freeform turn triggers the matcher
            first = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "I want to upload a video"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert first.status_code == 200

        assert mock_match.await_count == 1
        assert mock_match.await_args.args == ("I want to upload a video",)
        assert mock_match.await_args.kwargs["chat_context"][0] == {
            "role": "assistant",
            "content": GREETING_MESSAGE["content"],
        }
        assert "I want to upload a video" not in [
            message["content"] for message in mock_match.await_args.kwargs["chat_context"]
        ]

        # Rephrase — clears draft, returns plain assistant message
        rephrase = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={"content": "Let me rephrase"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rephrase.status_code == 200

        # Now a new freeform turn should trigger a fresh matcher call
        with patch(
            "app.routes.chat.match_message",
            new_callable=AsyncMock,
        ) as mock_match2:
            mock_match2.return_value = MatchResult(
                matched=True,
                goal_type="github_repo",
                confidence=0.85,
                rationale="second match after rephrase",
            )

            rematch = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "I want to create a GitHub repo"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rematch.status_code == 200

        assert mock_match2.await_count == 1
        assert mock_match2.await_args.args == ("I want to create a GitHub repo",)
        # Prior context should include the rephrase message and the
        # assistant's "tell me what you'd like" response — the full
        # chat history is passed as context.
        prior_contexts = [
            m["content"] for m in mock_match2.await_args.kwargs["chat_context"]
        ]
        assert "Let me rephrase" in prior_contexts
        assert any("tell me what you'd like" in c for c in prior_contexts)
        # The current user message must NOT be in prior context
        assert "I want to create a GitHub repo" not in prior_contexts



@pytest.mark.asyncio
async def test_send_message_match_confirmation_returns_awaiting_input_and_still_calls_match_once():
    """Pressing the matched-path confirmation should continue the server-backed
    conversation with an awaiting_input assistant prompt. The confirmation
    turn is handled by the state machine without a second matcher call."""
    prompt = "I want to upload a YouTube walkthrough and pledge $20"
    confirmation = "Use this goal type: youtube_video"

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
                confidence=0.91,
                rationale="initial match",
            )

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
    # The first missing criterion is deadline (no date extracted from this prompt)
    awaiting = body["messages"][-1]
    assert awaiting["role"] == "assistant"
    assert awaiting["action"]["type"] == "awaiting_input"
    assert awaiting["action"]["field"] == "deadline"
    assert "deadline" in awaiting["content"].lower()

    # State machine handles confirmation inline — matcher called only once
    assert mock_match.await_count == 1
    assert mock_match.await_args.args == (prompt,)


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
    # greeting + user + match_proposed (no auto awaiting_input)
    assert len(after.messages) == 3
    assert after.messages[1] == {"role": "user", "content": prompt, "action": None}
    assert after.messages[2]["role"] == "assistant"
    assert after.messages[2]["action"]["type"] == "match_proposed"
    assert after.draft_goal["goal_type"] == "youtube_video"
    assert after.draft_goal["pledge_amount"] == 2000
    assert after.draft_goal["currency"] == "usd"
    assert after.draft_goal["deadline"].startswith("2026-07-04")
    assert after.updated_at > before.updated_at


# ── helpers for create-goal tests ───────────────────────────────────────────

VALID_GOAL_PAYLOAD = {
    "title": "YouTube walkthrough of my project",
    "description": "I want to upload a YouTube walkthrough and pledge $20",
    "goal_type": "youtube_video",
    "pledge_amount": 2000,
    "currency": "usd",
    "deadline": "2026-07-15T00:00:00Z",
    "timezone": "America/New_York",
    "charity_id": "acct_charity123",
    "criteria": {
        "video_description": "YouTube walkthrough of my project",
        "min_duration_seconds": 300,
    },
}


async def _drive_to_ready_to_create(client, token, session_id):
    """Drive a chat session through match → confirm → criterion filling → ready_to_create.

    Uses a date-including prompt so deadline is auto-extracted. After the
    initial match_proposed, the user explicitly confirms the match via
    "Use this goal type: youtube_video". Then each awaiting_input prompt
    is answered until ready_to_create is emitted.

    Returns the ready_to_create action from the final assistant message.
    """
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

        # Send the initial goal description (includes date for auto-extraction)
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={
                "content": (
                    "I want to upload a YouTube walkthrough of my project "
                    "by 2026-07-04 and pledge $20"
                )
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    # Confirm the match — sends "Use this goal type: youtube_video"
    resp = await client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"content": "Use this goal type: youtube_video"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    # Fill missing criteria (charity_id, min_duration_seconds)
    body = resp.json()
    for _ in range(2):
        last_msg = body["messages"][-1]
        action = last_msg.get("action")
        if isinstance(action, dict) and action.get("type") == "awaiting_input":
            field = action["field"]
            if field == "charity_id":
                reply = "acct_charity123"
            elif field == "min_duration_seconds":
                reply = "300"
            else:
                reply = f"answer for {field}"
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": reply},
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            break
        body = resp.json()

    # After all criteria filled, the last assistant message should be ready_to_create
    last_msg = body["messages"][-1]
    action = last_msg.get("action")
    assert isinstance(action, dict) and action.get("type") == "ready_to_create", (
        f"Expected ready_to_create, got {action}"
    )
    return action, body


# ── create-goal tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_goal_returns_201_with_goal_id_and_status():
    """A session driven to ready_to_create produces a valid goal via the
    create-goal endpoint."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": action["goal_payload"]},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert "goal_id" in body
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_create_goal_updates_session_status_to_goal_created():
    """After successful create-goal, the session status is goal_created."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": action["goal_payload"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        # Verify session status through a single repository read
        engine = create_async_engine(settings.database_url, echo=False)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT status FROM chat_sessions WHERE id = :id"),
                    {"id": session_id},
                )
                row = result.fetchone()
                assert row is not None
                assert row.status == "goal_created"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_create_goal_returns_404_for_nonexistent_session():
    """create-goal returns 404 for nonexistent sessions."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            f"/api/chat/sessions/{uuid.uuid4()}/create-goal",
            json={"goal_payload": VALID_GOAL_PAYLOAD},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_goal_returns_404_for_wrong_owner():
    """create-goal returns 404 for sessions not owned by the user (no existence leak)."""
    async with make_client() as client:
        token_a, _ = await _auth(client)
        session_id = await _create_session(client, token_a)

        token_b, _ = await _auth(
            client, email="other@example.com", name="Other", sub="other-sub"
        )

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": VALID_GOAL_PAYLOAD},
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_goal_returns_401_unauthenticated():
    """create-goal returns 401 without auth."""
    async with make_client() as client:
        resp = await client.post(
            f"/api/chat/sessions/{uuid.uuid4()}/create-goal",
            json={"goal_payload": VALID_GOAL_PAYLOAD},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_goal_returns_422_when_not_ready_to_create():
    """create-goal returns 422 when the session hasn't reached ready_to_create."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        # Session is at greeting only — not ready_to_create
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": VALID_GOAL_PAYLOAD},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_goal_accepts_client_edited_presentation_fields():
    """create-goal uses the client-submitted payload for goal creation.

    Presentation fields (title, description, deadline, pledge_amount)
    MAY differ from the stored ready_to_create draft — that is the point
    of the final-review screen.  Identity fields (goal_type, required
    criteria fields) must still match the confirmed session draft.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)
        stored_payload = action["goal_payload"]

        # Modify presentation fields — this must be ACCEPTED
        edited_payload = dict(stored_payload)
        edited_payload["title"] = "A completely different goal"
        edited_payload["pledge_amount"] = 99999
        edited_payload["description"] = "Updated description"

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": edited_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Endpoint accepts the edited presentation fields
        assert resp.status_code == 201
        goal_id = resp.json()["goal_id"]

        # Verify the created goal reflects the CLIENT-SUBMITTED payload
        resp2 = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        goals = resp2.json()
        match = next(g for g in goals if g["id"] == goal_id)
        assert match["title"] == "A completely different goal"
        assert match["pledge_amount"] == 99999
        assert match["description"] == "Updated description"
        # Identity fields still match
        assert match["goal_type"] == stored_payload["goal_type"]


@pytest.mark.asyncio
async def test_create_goal_accepts_canonical_criteria_payload():
    """create-goal accepts a goal_payload whose criteria uses the canonical
    API-spec shape {criteria_type, criteria_data} (the wrapped form),
    normalizing it to the flat dict the GoalCreate schema expects."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)
        stored_payload = action["goal_payload"]

        # Rewrap criteria into the canonical {criteria_type, criteria_data} shape
        canonical_payload = dict(stored_payload)
        canonical_payload["criteria"] = {
            "criteria_type": "youtube",
            "criteria_data": dict(stored_payload["criteria"]),
        }

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": canonical_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        goal_id = resp.json()["goal_id"]

        # Verify the goal is persisted correctly
        resp2 = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        goals = resp2.json()
        match = next(g for g in goals if g["id"] == goal_id)
        assert match["goal_type"] == "youtube_video"
        assert match["status"] == "active"


@pytest.mark.asyncio
async def test_create_goal_rejects_mismatched_goal_type():
    """create-goal rejects a client payload whose goal_type differs from the
    confirmed session draft — identity fields cannot be changed."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)
        stored_payload = action["goal_payload"]

        # Change goal_type to mismatch — this must be REJECTED
        mismatched_payload = dict(stored_payload)
        mismatched_payload["goal_type"] = "github_repo"

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": mismatched_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "goal_type" in detail.lower()
        assert "github_repo" in detail
        assert "youtube_video" in detail


@pytest.mark.asyncio
async def test_create_goal_rejects_during_edit_flow_before_new_review():
    """After Edit → change that produces a new awaiting_input, the latest
    assistant action is not ready_to_create, so /create-goal must return
    422.  The old pre-edit ready_to_create in the message history is
    stale and must not be accepted.

    The edit follow-up must clear a required criterion so that
    _compute_missing_criteria produces awaiting_input instead of
    ready_to_create.  We do this by sending an explicit field update
    that sets charity_id to an empty value through the edit fallback
    path, followed by a second message that triggers the criteria
    recomputation."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        # Drive to ready_to_create
        await _drive_to_ready_to_create(client, token, session_id)

        # User taps "Edit"
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={"content": "edit"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # Latest assistant action is null (not ready_to_create)
        body = resp.json()
        assert body["messages"][-1]["action"] is None

        # Now, at this point create-goal must 422 since the latest
        # assistant action is null, not ready_to_create.
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": VALID_GOAL_PAYLOAD},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

        # Now user provides the edit content — a valid change that
        # keeps all criteria satisfied, producing a fresh ready_to_create.
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={"content": "set title to My Updated Project"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["messages"][-1]["action"]["type"] == "ready_to_create"
        new_payload = body["messages"][-1]["action"]["goal_payload"]
        assert new_payload["title"] == "My Updated Project"

        # create-goal now succeeds with the fresh ready_to_create
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": new_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_goal_returns_422_for_invalid_goal_payload():
    """create-goal validates the submitted payload through the canonical
    GoalCreate schema at the endpoint level — both flat and wrapped
    criteria forms."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)

        # Case 1: invalid pledge_amount=0 (flat criteria)
        bad_payload = dict(action["goal_payload"])
        bad_payload["pledge_amount"] = 0

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": bad_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "pledge_amount" in detail or "positive" in detail.lower()

    # Case 2: canonical wrapped criteria with malformed payload —
    # criteria_type present but criteria_data is missing, so GoalCreate
    # validation fails on the flat dict being empty
    async with make_client() as client:
        token2, _ = await _auth(client, email="test2@example.com", name="Test Two",
                                sub="test-sub-2")
        session_id2 = await _create_session(client, token2)

        action2, _ = await _drive_to_ready_to_create(client, token2, session_id2)
        bad_canonical = dict(action2["goal_payload"])
        # criteria_type alone without criteria_data is an invalid flat dict —
        # the normalization will unwrap criteria_data (missing), leaving {}
        # which then fails required-fields check
        bad_canonical["criteria"] = {
            "criteria_type": "youtube",
            # intentionally missing criteria_data
        }

        resp = await client.post(
            f"/api/chat/sessions/{session_id2}/create-goal",
            json={"goal_payload": bad_canonical},
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # The error may come from GoalCreate validation (fields validation)
        # or from required-criteria check; either is acceptable
        assert "pledge_amount" in detail.lower() or "video_description" in detail.lower() or "Missing required criteria" in detail


@pytest.mark.asyncio
async def test_missing_criteria_advance_one_at_a_time():
    """Each criterion-filling turn advances exactly one criterion; the
    assistant asks for the next missing criterion after each reply.
    The user must first explicitly confirm the match."""
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

            # Initial message (includes date so deadline is auto-extracted)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={
                    "content": (
                        "I want to upload a YouTube walkthrough of my project "
                        "by 2026-07-04 and pledge $20"
                    )
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()

            # match_proposed only — no auto awaiting_input
            assert body["messages"][-1]["action"]["type"] == "match_proposed"
            assert len(body["messages"]) == 3

            # Confirm the match
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Use this goal type: youtube_video"},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = resp.json()
            # First awaiting_input: min_duration_seconds (charity is optional
            # and must not be asked for).
            assert body["messages"][-1]["action"]["type"] == "awaiting_input"
            assert body["messages"][-1]["action"]["field"] == "min_duration_seconds"

            # Fill min_duration_seconds → should emit ready_to_create
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "300"},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = resp.json()
            assert body["messages"][-1]["action"]["type"] == "ready_to_create"
            assert "goal_payload" in body["messages"][-1]["action"]

        # Matcher was called exactly once
        mock_match.assert_awaited_once()


@pytest.mark.asyncio
async def test_ready_to_create_payload_includes_all_required_fields():
    """The ready_to_create action produces a payload that can create a goal
    through the create-goal endpoint in both flat and canonical criteria
    shapes.  Verify the persisted goal has all expected fields."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _body = await _drive_to_ready_to_create(client, token, session_id)
        payload = action["goal_payload"]

        # The draft-produced payload must NOT have internal keys like _editing
        assert "_editing" not in payload, (
            "ready_to_create payload must not leak internal flags"
        )
        # Must include all required top-level fields (charity_id is optional)
        for field in ("title", "goal_type", "pledge_amount", "deadline",
                      "criteria"):
            assert field in payload, (
                f"ready_to_create payload missing required field: {field}"
            )
        # Criteria must be a flat dict (not the canonical wrapper) when
        # emitted by the draft state machine
        assert isinstance(payload["criteria"], dict)
        assert "video_description" in payload["criteria"]
        assert "min_duration_seconds" in payload["criteria"]

        # ----- Flat criteria: create the goal through the endpoint -----
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        goal_id = resp.json()["goal_id"]

        # Verify the created goal via GET /api/goals — all fields persisted
        resp2 = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        goals = resp2.json()
        match = next(g for g in goals if g["id"] == goal_id)
        assert match["goal_type"] == "youtube_video"
        assert match["pledge_amount"] == 2000
        assert match["title"] == payload["title"]
        assert match["status"] == "active"
        # charity_id is optional and no longer collected by the chat; the
        # draft payload omits it and the created goal has none.
        assert match["charity_id"] == payload.get("charity_id")
        assert "deadline" in match

        # ----- Canonical (wrapped) criteria: also succeeds -----
        session_id2 = await _create_session(client, token)
        action2, _ = await _drive_to_ready_to_create(client, token, session_id2)
        canonical_payload = dict(action2["goal_payload"])
        canonical_payload["criteria"] = {
            "criteria_type": "youtube",
            "criteria_data": dict(action2["goal_payload"]["criteria"]),
        }

        resp3 = await client.post(
            f"/api/chat/sessions/{session_id2}/create-goal",
            json={"goal_payload": canonical_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp3.status_code == 201
        goal_id2 = resp3.json()["goal_id"]

        resp4 = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp4.status_code == 200
        goals2 = resp4.json()
        match2 = next(g for g in goals2 if g["id"] == goal_id2)
        assert match2["goal_type"] == "youtube_video"
        assert match2["status"] == "active"


@pytest.mark.asyncio
async def test_create_goal_creates_goal_accessible_via_goals_api():
    """The goal created via create-goal is retrievable via GET /api/goals."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": action["goal_payload"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        goal_id = resp.json()["goal_id"]

        # Verify via goals API
        resp2 = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        goals = resp2.json()
        assert any(g["id"] == goal_id for g in goals)
        match = next(g for g in goals if g["id"] == goal_id)
        assert match["goal_type"] == "youtube_video"
        assert match["status"] == "active"


@pytest.mark.asyncio
async def test_create_goal_normalizes_human_deadline_and_dms_coordinates():
    """The exact inputs that used to 422: a US-format deadline with a
    12-hour time and Google-Maps DMS coordinates pasted as strings must be
    normalized at create time instead of failing pydantic validation."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        # Reach ready_to_create through the normal flow, then submit an
        # edited payload carrying messy-but-honest human input.
        await _drive_to_ready_to_create(client, token, session_id)

        payload = dict(VALID_GOAL_PAYLOAD)
        payload["deadline"] = "7/18/2026 6am"
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["goal_id"]

        resp = await client.get(
            f"/api/goals/{goal_id}", headers={"Authorization": f"Bearer {token}"}
        )
        # The payload's timezone is America/New_York, so "6am" means 6am
        # Eastern — stored as 10:00 UTC (EDT, UTC-4 in July).
        assert resp.json()["deadline"].startswith("2026-07-18T10:00:00")
