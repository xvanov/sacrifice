"""Endpoint contract tests for POST /api/chat/sessions.

These tests validate the api_spec.md contract:
- 401 for unauthenticated requests (also proves router registration)
- 201 with exact response shape on success
- Persisted state matches returned state (catches response-reconstruction drift)
- draft_goal is initialized to a non-null default for later turns
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models import ChatSession
from app.database import get_db


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _auth(client, email="chat-test@example.com", name="Chat Tester",
                sub="chat-test-sub", token="valid-chat-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        return resp.json()["access_token"]


@asynccontextmanager
async def _test_db():
    """Resolve the test DB session via the app's dependency override."""
    override_gen = app.dependency_overrides[get_db]()
    db = await override_gen.__anext__()
    try:
        yield db
    finally:
        try:
            await override_gen.__anext__()
        except StopAsyncIteration:
            pass


class TestCreateSessionUnauthenticated:
    async def test_returns_401_without_token(self):
        """Proves both auth enforcement and router registration.

        A 404/405 would mean the route isn't wired; 401 means the router is
        registered and auth is enforced — exactly the coverage the reviewer
        asked us to fold into this test.
        """
        async with _client() as client:
            resp = await client.post("/api/chat/sessions")
        assert resp.status_code == 401


class TestCreateSessionSuccess:
    async def test_returns_201_with_spec_shape(self):
        """Response must match api_spec.md exactly.

        The spec mandates:
        - session_id is a UUID string
        - messages[0] is the assistant greeting with role, content, action=null
        - status is "active"
        """
        async with _client() as client:
            token = await _auth(client)
            resp = await client.post(
                "/api/chat/sessions",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 201
        body = resp.json()

        # Top-level keys
        assert set(body.keys()) == {"session_id", "messages", "status"}

        # session_id is a non-empty UUID string
        assert isinstance(body["session_id"], str)
        assert len(body["session_id"]) > 0

        # status is "active"
        assert body["status"] == "active"

        # messages has exactly one entry: the assistant greeting
        assert isinstance(body["messages"], list)
        assert len(body["messages"]) == 1
        msg = body["messages"][0]
        assert msg["role"] == "assistant"
        assert msg["content"] == (
            "Tell me what you want to do, and I'll figure out how to track it."
        )
        assert msg["action"] is None

    async def test_persisted_session_matches_response(self):
        """DB row must match the API response — guards against response drift.

        The reviewer flagged that the current route reconstructs the greeting
        payload instead of returning persisted session.messages. This test
        reads the session back from the DB and compares, catching any drift
        between stored and returned state.
        """
        async with _client() as client:
            token = await _auth(client)
            resp = await client.post(
                "/api/chat/sessions",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 201
        body = resp.json()
        session_id = body["session_id"]

        # Read back from DB via the app's dependency override
        async with _test_db() as db:
            result = await db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            session = result.scalar_one_or_none()

        assert session is not None, "Session not found in DB after creation"
        assert str(session.id) == session_id
        assert session.status == "active"
        assert session.user_id is not None

        # Persisted messages must match what the API returned
        assert session.messages == body["messages"], (
            f"DB messages {session.messages!r} drifted from "
            f"response messages {body['messages']!r}"
        )

    async def test_draft_goal_is_initialized_not_null(self):
        """New sessions must initialize draft_goal for later turns.

        The current model has no default for draft_goal, so newly created
        rows leave it NULL. The story expects an initialized empty dict so
        that subsequent message turns can populate draft criteria.
        """
        async with _client() as client:
            token = await _auth(client)
            resp = await client.post(
                "/api/chat/sessions",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 201
        session_id = resp.json()["session_id"]

        async with _test_db() as db:
            result = await db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            session = result.scalar_one_or_none()

        assert session is not None
        assert session.draft_goal is not None, (
            "draft_goal must be initialized to an empty dict, not NULL. "
            "The model currently has no default for this column."
        )
        assert isinstance(session.draft_goal, dict)
        assert session.draft_goal == {}, (
            f"draft_goal should be empty dict, got {session.draft_goal!r}"
        )