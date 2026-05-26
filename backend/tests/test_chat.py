from unittest.mock import patch

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


# ── helpers ────────────────────────────────────────────────────────


async def _create_session(client, token):
    resp = await client.post(
        "/api/chat/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp


async def _post_message(client, token, session_id, content):
    resp = await client.post(
        f"/api/chat/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": content},
    )
    return resp


async def _post_confirm_match(client, token, session_id):
    """Simulate the user tapping 'Use this' on the match_proposed card."""
    return await _post_message(client, token, session_id, "Yes, use that goal type.")


async def _post_criterion_reply(client, token, session_id, value):
    """Simulate the user replying to an awaiting_input prompt."""
    return await _post_message(client, token, session_id, value)


# ── draft filling tests ────────────────────────────────────────────


async def test_missing_criteria_advance_one_at_a_time():
    """After a match is accepted, each user reply fills ONE criterion at a time.

    The assistant must return exactly one `awaiting_input` action per turn,
    and the `draft_goal` must grow with each reply.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        # 1. Create session
        sess_resp = await _create_session(client, token)
        assert sess_resp.status_code == 201
        session_id = sess_resp.json()["session_id"]

        # 2. Send goal-description message
        msg_resp = await _post_message(
            client, token, session_id,
            "I want to upload a YouTube walkthrough by Friday and pledge $20",
        )
        assert msg_resp.status_code == 200
        messages = msg_resp.json()["messages"]
        assistant_msg = [m for m in messages if m["role"] == "assistant"][-1]
        assert assistant_msg["action"]["type"] == "match_proposed"
        assert assistant_msg["action"]["goal_type"] == "youtube_video"
        missing = assistant_msg["action"]["missing_criteria"]
        assert len(missing) > 0, "Should have missing criteria after initial match"

        # 3. Accept the match
        accept_resp = await _post_confirm_match(client, token, session_id)
        assert accept_resp.status_code == 200
        accept_messages = accept_resp.json()["messages"]
        accept_assistant = [m for m in accept_messages if m["role"] == "assistant"][-1]

        # 4. After accepting, assistant should ask for ONE criterion
        assert accept_assistant["action"]["type"] == "awaiting_input"
        first_field = accept_assistant["action"]["field"]
        assert first_field in missing

        # 5. Reply with a value for that criterion
        reply_resp = await _post_criterion_reply(client, token, session_id, "2026-06-01T17:00:00Z")
        assert reply_resp.status_code == 200
        reply_messages = reply_resp.json()["messages"]
        reply_assistant = [m for m in reply_messages if m["role"] == "assistant"][-1]

        # 6. Assistant should ask for the NEXT criterion (not the same one)
        if reply_assistant["action"]["type"] == "awaiting_input":
            second_field = reply_assistant["action"]["field"]
            assert second_field != first_field, (
                f"Should advance to next criterion, got same field '{first_field}'"
            )
            assert second_field in missing


async def test_completed_draft_returns_ready_to_create():
    """When all required criteria are filled, the assistant returns ready_to_create
    with a full goal_payload suitable for POST /api/goals.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        # Send goal description and get match
        await _post_message(
            client, token, session_id,
            "I want to upload a YouTube walkthrough by Friday and pledge $20",
        )

        # Accept the match
        await _post_confirm_match(client, token, session_id)

        # Fill each criterion until ready_to_create — mock by sending enough replies
        # The api_spec says missing_criteria for youtube_video are:
        # charity_id, deadline, video_description
        # We'll reply to each awaiting_input prompt and check final state
        final_action_type = None
        for reply_value in [
            "2026-06-01T17:00:00Z",   # deadline
            "acct_charity123",         # charity_id
            "A walkthrough of my project",  # video_description
        ]:
            resp = await _post_message(client, token, session_id, reply_value)
            assert resp.status_code == 200
            assistant_msgs = [m for m in resp.json()["messages"] if m["role"] == "assistant"]
            final_action_type = assistant_msgs[-1]["action"]["type"]

        assert final_action_type == "ready_to_create", (
            f"Expected ready_to_create after all criteria filled, got {final_action_type}"
        )

        # The last assistant message should carry a full goal_payload
        last_msg = [m for m in resp.json()["messages"] if m["role"] == "assistant"][-1]
        assert "goal_payload" in last_msg["action"]
        goal_payload = last_msg["action"]["goal_payload"]
        assert goal_payload["goal_type"] == "youtube_video"
        assert goal_payload["pledge_amount"] == 2000
        assert "deadline" in goal_payload
        assert "charity_id" in goal_payload
        assert "criteria" in goal_payload


# ── create-goal tests ──────────────────────────────────────────────


async def test_create_goal_success_returns_goal_id():
    """POST /api/chat/sessions/{id}/create-goal with a valid goal_payload
    returns 201 with goal_id and status, and the session status becomes goal_created.
    Also verifies that a nonexistent session id returns 404.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        # Verify route exists: nonexistent session must return app-level 404
        # (not a framework-level "route not found" 404)
        bogus_resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json={"goal_payload": {"title": "x", "deadline": "2026-01-01T00:00:00Z",
                                   "pledge_amount": 100, "goal_type": "youtube_video",
                                   "criteria": {}}},
        )
        assert bogus_resp.status_code == 404
        body = bogus_resp.json()
        # App-level errors include detail; a raw "route not found" from
        # the framework means the route was never registered.
        assert "detail" in body
        assert "not found" in body["detail"].lower()

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        valid_payload = {
            "goal_payload": {
                "title": "YouTube walkthrough",
                "description": "A walkthrough demo",
                "goal_type": "youtube_video",
                "pledge_amount": 2000,
                "currency": "usd",
                "deadline": "2026-06-01T00:00:00Z",
                "timezone": "America/New_York",
                "charity_id": "acct_charity123",
                "criteria": {
                    "min_duration_seconds": 300,
                    "video_description": "A walkthrough demo",
                },
            }
        }

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json=valid_payload,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "goal_id" in body
        assert body["status"] == "active"

        # Verify session status is now goal_created
        get_resp = await client.get(
            f"/api/chat/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "goal_created"


async def test_invalid_goal_payload_returns_422():
    """POST /api/chat/sessions/{id}/create-goal with an invalid goal_payload
    returns 422, delegating to the existing GoalCreate validation.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        # Missing required fields like title, deadline, pledge_amount, criteria
        invalid_payload = {
            "goal_payload": {
                "goal_type": "youtube_video",
                "currency": "usd",
            }
        }

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json=invalid_payload,
        )
        assert resp.status_code == 422


# ── auth / 404 tests ───────────────────────────────────────────────


async def test_create_session_requires_auth():
    async with make_client() as client:
        resp = await client.post("/api/chat/sessions")
    assert resp.status_code == 401


async def test_post_message_empty_content_returns_422():
    async with make_client() as client:
        token, _ = await _auth(client)
        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        resp = await _post_message(client, token, session_id, "   ")
    assert resp.status_code == 422