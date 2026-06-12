from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.config import settings
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


async def _get_session(client, token, session_id):
    resp = await client.get(
        f"/api/chat/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp


async def _post_confirm_match(client, token, session_id):
    """Simulate the user tapping 'Use this' on the match_proposed card."""
    return await _post_message(client, token, session_id, "Yes, use that goal type.")


def _last_assistant(messages):
    return [m for m in messages if m["role"] == "assistant"][-1]


def _criterion_value_for_field(field: str) -> str:
    """Return a plausible value for each known criterion field."""
    values = {
        "deadline": "2026-06-01T17:00:00Z",
        "charity_id": "acct_charity123",
        "video_description": "A walkthrough of my project",
        "url": "https://example.com/api/health",
        "method": "GET",
        "expected_status": "200",
        "expected_body_schema": '{"status":"ok"}',
        "headers": "{}",
        "repo_owner": "test-owner",
        "repo_name": "test-repo",
        "branch": "main",
        "repo_url": "https://github.com/test-owner/test-repo",
        "test_command": "pytest",
        "language": "python",
        "env_vars": "{}",
        "goal_description": "Test sandbox goal",
    }
    return values.get(field, f"value-for-{field}")


# ── session creation tests ─────────────────────────────────────────


async def test_create_session_requires_auth():
    """Unauthenticated session creation returns 401."""
    async with make_client() as client:
        resp = await client.post("/api/chat/sessions")
    assert resp.status_code == 401


async def test_create_session_returns_valid_contract():
    """Session creation returns 201 with expected fields and initial greeting."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await _create_session(client, token)

    assert resp.status_code == 201
    body = resp.json()
    assert "session_id" in body
    assert body["status"] == "active"
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert body["messages"][0]["action"] is None
    assert "figure out how to track it" in body["messages"][0]["content"]


# ── session ownership tests ────────────────────────────────────────


async def test_other_user_session_post_message_returns_403():
    """POST messages for another user's session returns 403 per the API spec."""
    async with make_client() as client:
        token_a, _ = await _auth(client, email="a@example.com", sub="sub-a",
                                 token="token-a")
        token_b, _ = await _auth(client, email="b@example.com", sub="sub-b",
                                 token="token-b")

        sess_resp = await _create_session(client, token_a)
        session_id = sess_resp.json()["session_id"]

        resp = await _post_message(client, token_b, session_id,
                                   "I want to post to someone else's session")
        assert resp.status_code == 403
        body = resp.json()
        assert "detail" in body


async def test_other_user_session_create_goal_returns_404():
    """POST create-goal for another user's session returns 404 per the API spec.

    The documented contract for create-goal only lists 401 / 404 / 422,
    so the endpoint must not leak session existence via 403.
    """
    async with make_client() as client:
        token_a, _ = await _auth(client, email="a@example.com", sub="sub-a",
                                 token="token-a")
        token_b, _ = await _auth(client, email="b@example.com", sub="sub-b",
                                 token="token-b")

        sess_resp = await _create_session(client, token_a)
        session_id = sess_resp.json()["session_id"]

        create_resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"goal_payload": {
                "title": "x",
                "deadline": "2026-01-01T00:00:00Z",
                "pledge_amount": 100,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "test"},
            }},
        )
        assert create_resp.status_code == 404
        create_body = create_resp.json()
        assert "detail" in create_body
        assert "not found" in create_body["detail"].lower()


async def test_nonexistent_session_returns_404():
    """Accessing a non-existent session returns 404 for any authenticated user."""
    async with make_client() as client:
        token, _ = await _auth(client)

        nonexistent_id = "00000000-0000-0000-0000-000000000000"
        resp = await _post_message(client, token, nonexistent_id, "hello")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        assert "not found" in body["detail"].lower()


# ── draft filling tests ────────────────────────────────────────────


async def test_missing_criteria_advance_one_at_a_time():
    """After a match is accepted, each user reply fills ONE criterion at a time.

    Verifies conversationally that the assistant returns one awaiting_input
    per turn, that draft_goal and session messages are persisted after each
    turn, and that criteria are consumed without repeats.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        # 1. Create session
        sess_resp = await _create_session(client, token)
        assert sess_resp.status_code == 201
        session_id = sess_resp.json()["session_id"]

        # 2. Send goal-description message — drive matching with a mock
        # so the test is deterministic regardless of LLM configuration.
        with patch("app.routes.chat.match_goal_type") as mock_match:
            mock_match.return_value = {
                "match": "youtube_video",
                "confidence": 0.87,
                "rationale": "test mock",
            }
            msg_resp = await _post_message(
                client, token, session_id,
                "I want to upload a YouTube walkthrough by Friday and pledge $20",
            )
        assert msg_resp.status_code == 200
        messages = msg_resp.json()["messages"]
        assistant_msg = _last_assistant(messages)
        assert assistant_msg["action"]["type"] == "match_proposed"
        assert assistant_msg["action"]["goal_type"] == "youtube_video"
        missing = assistant_msg["action"]["missing_criteria"]
        assert len(missing) > 0, "Should have missing criteria after initial match"

        # Verify session persistence after match
        get_resp = await _get_session(client, token, session_id)
        assert get_resp.status_code == 200
        persisted = get_resp.json()
        # greeting + user_msg + match_proposed assistant
        assert len(persisted["messages"]) == 3
        assert persisted["draft_goal"] is not None
        assert persisted["draft_goal"]["goal_type"] == "youtube_video"

        # 3. Accept the match
        accept_resp = await _post_confirm_match(client, token, session_id)
        assert accept_resp.status_code == 200
        accept_messages = accept_resp.json()["messages"]
        accept_assistant = _last_assistant(accept_messages)

        # 4. After accepting, assistant should ask for ONE criterion
        assert accept_assistant["action"]["type"] == "awaiting_input"
        first_field = accept_assistant["action"]["field"]
        assert first_field in missing

        # Verify session persistence after acceptance
        get_resp = await _get_session(client, token, session_id)
        persisted = get_resp.json()
        assert len(persisted["messages"]) >= 3  # greeting + match + accept + awaiting

        # 5. Reply with a value for that prompted criterion
        first_value = _criterion_value_for_field(first_field)
        reply_resp = await _post_message(client, token, session_id, first_value)
        assert reply_resp.status_code == 200
        reply_messages = reply_resp.json()["messages"]
        reply_assistant = _last_assistant(reply_messages)

        # Verify draft_goal persisted the filled criterion
        get_resp = await _get_session(client, token, session_id)
        persisted = get_resp.json()
        if first_field in ("deadline", "charity_id"):
            assert persisted["draft_goal"].get(first_field) == first_value
        else:
            assert persisted["draft_goal"].get("criteria", {}).get(first_field) == first_value

        # 6. Assistant should ask for the NEXT criterion (not the same one)
        if reply_assistant["action"]["type"] == "awaiting_input":
            second_field = reply_assistant["action"]["field"]
            assert second_field != first_field, (
                f"Should advance to next criterion, got same field '{first_field}'"
            )
            assert second_field in missing


async def test_completed_draft_returns_ready_to_create():
    """When all required criteria are filled, assistant returns ready_to_create
    with a full goal_payload.

    Reads each awaiting_input field from the response and sends a matching
    value. Tracks which values were sent and verifies they appear in the
    final goal_payload — not just that keys exist.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        # Send goal description — drive matching with a mock so the test
        # is deterministic regardless of LLM configuration.
        with patch("app.routes.chat.match_goal_type") as mock_match:
            mock_match.return_value = {
                "match": "youtube_video",
                "confidence": 0.87,
                "rationale": "test mock",
            }
            await _post_message(
                client, token, session_id,
                "I want to upload a YouTube walkthrough by Friday and pledge $20",
            )

        # Accept the match — this triggers the first awaiting_input
        accept_resp = await _post_confirm_match(client, token, session_id)
        assert accept_resp.status_code == 200
        body = accept_resp.json()
        assistant = _last_assistant(body["messages"])

        # Track which values we send for each field
        sent_values: dict[str, str] = {}

        max_turns = 10
        final_action_type = assistant["action"]["type"]
        for _ in range(max_turns):
            if final_action_type == "ready_to_create":
                break

            if final_action_type == "awaiting_input":
                field = assistant["action"]["field"]
                value = _criterion_value_for_field(field)
                sent_values[field] = value
                resp = await _post_message(client, token, session_id, value)
                assert resp.status_code == 200
                body = resp.json()
                assistant = _last_assistant(body["messages"])
                final_action_type = assistant["action"]["type"]
            else:
                # Unexpected action — stop and fail below
                break

        assert final_action_type == "ready_to_create", (
            f"Expected ready_to_create after all criteria filled, got {final_action_type}"
        )

        # The last assistant message should carry a full goal_payload
        assert "goal_payload" in assistant["action"]
        goal_payload = assistant["action"]["goal_payload"]
        assert goal_payload["goal_type"] == "youtube_video"
        assert goal_payload["pledge_amount"] == 2000

        # Verify each sent value actually landed in the payload
        for field, expected_value in sent_values.items():
            if field in ("deadline", "charity_id"):
                actual = goal_payload.get(field)
                assert actual == expected_value, (
                    f"goal_payload['{field}'] expected '{expected_value}', got '{actual}'"
                )
            else:
                criteria = goal_payload.get("criteria", {})
                actual = criteria.get(field)
                assert actual == expected_value, (
                    f"goal_payload.criteria['{field}'] expected '{expected_value}', got '{actual}'"
                )

        # Validate the goal_payload against the existing GoalCreate contract.
        # This ensures ready_to_create produces payloads that the real goal
        # creation endpoint will accept (required fields present, types correct).
        from app.schemas.goal import GoalCreate
        try:
            GoalCreate(**goal_payload)
        except Exception as e:
            raise AssertionError(
                f"goal_payload from ready_to_create is not valid against "
                f"GoalCreate: {e}"
            )


# ── no-match tests ─────────────────────────────────────────────────


async def test_no_match_turn_returns_no_match_action():
    """When no goal type matches, assistant returns no_match action with
    suggested_action generate_new_goal_type.

    Tests the below-threshold / none path in the chat turn handler:
    the assistant response shape, not the stub endpoint.

    Mocking match_goal_type to a deterministic no-match result keeps
    the test independent of local matcher heuristics.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        with patch("app.routes.chat.match_goal_type") as mock_match:
            mock_match.return_value = {
                "match": "none",
                "confidence": 0.0,
                "rationale": "No matching goal type found",
            }
            msg_resp = await _post_message(
                client, token, session_id,
                "Track that I drank 8 glasses of water today",
            )

        assert msg_resp.status_code == 200
        messages = msg_resp.json()["messages"]
        assistant = _last_assistant(messages)
        assert assistant["action"]["type"] == "no_match"
        assert "suggested_action" in assistant["action"]
        assert assistant["action"]["suggested_action"] == "generate_new_goal_type"


async def test_below_threshold_llm_match_returns_no_match():
    """When the LLM returns a named match below the confidence threshold,
    the full chain (_llm_match → match_goal_type → route) must surface it
    as a no_match action.

    Patches the lower-level _llm_match so the real match_goal_type
    threshold-preserving logic is exercised end-to-end.  Also patches
    settings so the Azure Foundry path is active during the test.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        # Patch _llm_match (the actual LLM transport layer) to return a
        # named match below threshold.  The real match_goal_type must
        # preserve this (not coerce to "none") and the route must decide
        # to treat it as no_match.
        with (
            patch("app.services.chat_match._llm_match") as mock_llm,
            patch.object(settings, "azure_foundry_endpoint", "https://test.invalid"),
            patch.object(settings, "azure_foundry_api_key", "test-key"),
        ):
            mock_llm.return_value = {
                "match": "youtube_video",
                "confidence": 0.45,
                "rationale": "Some keyword overlap but not enough",
            }
            msg_resp = await _post_message(
                client, token, session_id,
                "Track that I drank 8 glasses of water today",
            )

        assert msg_resp.status_code == 200
        messages = msg_resp.json()["messages"]
        assistant = _last_assistant(messages)
        assert assistant["action"]["type"] == "no_match", (
            f"Expected no_match, got {assistant['action']['type']}"
        )
        assert assistant["action"]["suggested_action"] == "generate_new_goal_type"


async def test_request_new_goal_type_stub_returns_501():
    """The request-new-goal-type endpoint is stubbed and returns 501.

    This is a separate concern from matching: the endpoint is always
    a stub regardless of how the user arrived at it.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        stub_resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt_summary": "Track that I drank 8 glasses of water today"},
        )
        assert stub_resp.status_code == 501
        body = stub_resp.json()
        assert "detail" in body
        assert "D010" in body["detail"]


async def test_request_new_goal_type_requires_auth():
    """Stubbed endpoint still requires authentication."""
    async with make_client() as client:
        resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/request-new-goal-type",
            json={"prompt_summary": "test"},
        )
        assert resp.status_code == 401


# ── create-goal tests ──────────────────────────────────────────────


async def test_create_goal_nonexistent_session_returns_404():
    """POST create-goal with a nonexistent session id returns 404."""
    async with make_client() as client:
        token, _ = await _auth(client)

        bogus_resp = await client.post(
            "/api/chat/sessions/00000000-0000-0000-0000-000000000000/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json={"goal_payload": {
                "title": "x",
                "deadline": "2026-01-01T00:00:00Z",
                "pledge_amount": 100,
                "goal_type": "youtube_video",
                "criteria": {},
            }},
        )
        assert bogus_resp.status_code == 404
        body = bogus_resp.json()
        assert "detail" in body
        assert "not found" in body["detail"].lower()


async def test_create_goal_success_returns_goal_id():
    """Drive the full conversational flow: match → confirm → fill criteria
    → ready_to_create → create-goal.  Asserts the new goal is accessible
    via GET /api/goals/{id} and the session is marked goal_created."""
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        # ── match phase ──────────────────────────────────────────
        with patch("app.routes.chat.match_goal_type") as mock_match:
            mock_match.return_value = {
                "match": "youtube_video",
                "confidence": 0.87,
                "rationale": "test mock",
            }
            await _post_message(
                client, token, session_id,
                "I want to upload a YouTube walkthrough by Friday and pledge $20",
            )

        # ── confirm match ────────────────────────────────────────
        accept_resp = await _post_confirm_match(client, token, session_id)
        assert accept_resp.status_code == 200
        body = accept_resp.json()
        assistant = _last_assistant(body["messages"])

        # ── fill every criterion conversationally ────────────────
        max_turns = 10
        for _ in range(max_turns):
            action_type = assistant["action"]["type"]
            if action_type == "ready_to_create":
                break
            if action_type == "awaiting_input":
                field = assistant["action"]["field"]
                value = _criterion_value_for_field(field)
                resp = await _post_message(client, token, session_id, value)
                assert resp.status_code == 200
                body = resp.json()
                assistant = _last_assistant(body["messages"])
            else:
                break

        assert assistant["action"]["type"] == "ready_to_create", (
            f"Expected ready_to_create, got {assistant['action']['type']}"
        )

        # ── create goal from the ready_to_create payload ─────────
        goal_payload = assistant["action"]["goal_payload"]
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            headers={"Authorization": f"Bearer {token}"},
            json={"goal_payload": goal_payload},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "goal_id" in body
        goal_id = body["goal_id"]
        assert body["status"] == "active"

        # Verify the goal exists via the canonical goal retrieval path
        get_goal_resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_goal_resp.status_code == 200
        goal = get_goal_resp.json()
        assert goal["goal_type"] == "youtube_video"
        assert goal["pledge_amount"] == 2000
        assert goal["status"] == "active"

        # Verify session status is now goal_created
        get_sess_resp = await _get_session(client, token, session_id)
        assert get_sess_resp.status_code == 200
        assert get_sess_resp.json()["status"] == "goal_created"


async def test_invalid_goal_payload_returns_422():
    """POST create-goal with an invalid goal_payload returns 422 and leaves
    the session in a non-goal_created state."""
    async with make_client() as client:
        token, _ = await _auth(client)

        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        # Missing required fields: title, deadline, pledge_amount, criteria
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

        # Session must NOT be in goal_created state after a failed creation
        get_sess_resp = await _get_session(client, token, session_id)
        assert get_sess_resp.status_code == 200
        assert get_sess_resp.json()["status"] != "goal_created", (
            "Session must not be marked goal_created after a failed validation"
        )


# ── message validation tests ───────────────────────────────────────


async def test_post_message_empty_content_returns_422():
    """Posting whitespace-only content returns 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        sess_resp = await _create_session(client, token)
        session_id = sess_resp.json()["session_id"]

        resp = await _post_message(client, token, session_id, "   ")
    assert resp.status_code == 422