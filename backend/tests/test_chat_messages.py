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

    # greeting + user + match_proposed + awaiting_input (auto-emitted for first missing criterion)
    assert len(body["messages"]) == 4
    assert body["messages"][0] == GREETING_MESSAGE
    assert body["messages"][-3] == {
        "role": "user",
        "content": prompt,
        "action": None,
    }

    match_proposed_msg = body["messages"][-2]
    assert match_proposed_msg["role"] == "assistant"
    assert match_proposed_msg["action"]["type"] == "match_proposed"
    assert match_proposed_msg["action"]["goal_type"] == "youtube_video"
    assert match_proposed_msg["action"]["confidence"] == 0.87
    assert set(match_proposed_msg["action"]["missing_criteria"]) == {
        "charity_id",
        "min_duration_seconds",
    }
    assert "criteria" not in match_proposed_msg["action"]["missing_criteria"]

    # Last message is the awaiting_input prompt for the first missing criterion
    awaiting_msg = body["messages"][-1]
    assert awaiting_msg["role"] == "assistant"
    assert awaiting_msg["action"]["type"] == "awaiting_input"
    assert awaiting_msg["action"]["field"] == "charity_id"

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
    without additional matcher calls."""
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

            first = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "first message"},
                headers={"Authorization": f"Bearer {token}"},
            )
            # Second message is caught by state machine — matcher not called again
            second = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "second message"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert mock_match.await_count == 1

    assert mock_match.await_args.args == ("first message",)
    assert mock_match.await_args.kwargs["chat_context"][0] == {
        "role": "assistant",
        "content": GREETING_MESSAGE["content"],
    }
    assert "first message" not in [
        message["content"] for message in mock_match.await_args.kwargs["chat_context"]
    ]



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
    # The first missing criterion is charity_id (no deadline was extracted from this prompt)
    awaiting = body["messages"][-1]
    assert awaiting["role"] == "assistant"
    assert awaiting["action"]["type"] == "awaiting_input"
    assert awaiting["action"]["field"] == "charity_id"
    assert "charity" in awaiting["content"].lower()

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
    # greeting + user + match_proposed + awaiting_input (auto-emitted)
    assert len(after.messages) == 4
    assert after.messages[1] == {"role": "user", "content": prompt, "action": None}
    assert after.messages[2]["role"] == "assistant"
    assert after.messages[2]["action"]["type"] == "match_proposed"
    assert after.messages[3]["role"] == "assistant"
    assert after.messages[3]["action"]["type"] == "awaiting_input"
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
        "criteria_type": "youtube",
        "criteria_data": {
            "video_description": "YouTube walkthrough of my project",
            "min_duration_seconds": 300,
        },
    },
}


async def _drive_to_ready_to_create(client, token, session_id):
    """Drive a chat session through match → criterion filling → ready_to_create.

    Uses a date-including prompt so deadline is auto-extracted. The user
    implicitly confirms the match by replying to the first awaiting_input
    prompt. After all criteria are filled the assistant emits ready_to_create.

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

    # Fill missing criteria (charity_id, min_duration_seconds)
    # The state machine auto-emits awaiting_input for the first missing
    # criterion after match_proposed.
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

        # Verify session status is persisted
        stored = await _load_session_state(session_id)
        assert stored is not None
        # We don't have a get-session endpoint, check the DB directly
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
async def test_create_goal_returns_422_for_invalid_payload():
    """create-goal delegates validation to GoalCreate — invalid payloads get 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, _ = await _drive_to_ready_to_create(client, token, session_id)

        # Mutate the payload to be invalid — missing required 'title'
        bad_payload = dict(action["goal_payload"])
        del bad_payload["title"]

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": bad_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_criteria_advance_one_at_a_time():
    """Each criterion-filling turn advances exactly one criterion; the
    assistant asks for the next missing criterion after each reply."""
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

            # match_proposed + awaiting_input (first missing criterion after
            # deadline extraction: charity_id)
            assert body["messages"][-2]["action"]["type"] == "match_proposed"
            assert body["messages"][-1]["action"]["type"] == "awaiting_input"
            assert body["messages"][-1]["action"]["field"] == "charity_id"

            # Fill charity_id → should advance to min_duration_seconds
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "acct_charity123"},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = resp.json()
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
    """The ready_to_create action payload includes all required goal fields
    and is valid against the GoalCreate schema."""
    async with make_client() as client:
        token, _ = await _auth(client)
        session_id = await _create_session(client, token)

        action, body = await _drive_to_ready_to_create(client, token, session_id)

        payload = action["goal_payload"]
        assert payload["title"] is not None
        assert payload["goal_type"] == "youtube_video"
        assert payload["pledge_amount"] == 2000
        assert "deadline" in payload
        assert "charity_id" in payload
        assert "criteria" in payload
        assert payload["criteria"]["min_duration_seconds"] == 300

        # Validate against GoalCreate — the draft criteria is a flat dict;
        # wrap it for schema validation if the goal type expects criteria_data.
        from app.schemas.goal import GoalCreate
        schema_payload = dict(payload)
        if "criteria" in schema_payload and isinstance(schema_payload["criteria"], dict):
            flat = schema_payload["criteria"]
            if "criteria_type" not in flat:
                schema_payload["criteria"] = {
                    "criteria_type": "youtube",
                    "criteria_data": flat,
                }
        validated = GoalCreate(**schema_payload)
        assert validated.goal_type == "youtube_video"
        assert validated.pledge_amount == 2000


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
