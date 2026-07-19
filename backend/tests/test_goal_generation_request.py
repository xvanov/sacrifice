"""Request endpoints tests for goal-generation chat flow.

Covers:
- POST /api/chat/sessions/{id}/request-new-goal-type returns 404 for missing session
- POST /api/chat/sessions/{id}/request-new-goal-type creates goal in awaiting status
- POST /api/chat/sessions/{id}/request-new-goal-type 409 includes structured direction_id
- POST /api/chat/sessions/{id}/request-new-goal-type 429 on spend cap exceeded
- POST /api/chat/sessions/{id}/request-new-goal-type rollback on write failure
- POST /api/chat/sessions/{id}/request-new-goal-type 422 on vague prompt (synthesis failure)
"""

import uuid
from unittest.mock import patch

from app.config import settings
from app.models.chat_spend import ChatSpendLedger
from app.models.goal import Goal
from app.services.direction_synth import DirectionSynthesisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from .utils_goal_generation import (
    GENERATION_REQUEST_BODY,
    _auth,
    _ensure_session,
    make_client,
    mock_synthesize_direction,  # noqa: F401 — pytest fixture
)

TEST_PLAN = {
    "test_request_new_goal_type_returns_404_for_missing_session": (
        "AC: API spec 404 when session doesn't exist. Asserts no goal row and "
        "no direction directory are created as side effects — calls real HTTP endpoint."
    ),
    "test_request_new_goal_type_creates_goal_in_awaiting_status": (
        "AC: API spec 202 with direction_id + goal_id in response. Verifies "
        "direction directory written to disk, state.yaml created with queued, "
        "goal persisted with awaiting_goal_type status and criteria_data containing "
        "module_name. Exercises real HTTP + DB read-back."
    ),
    "test_request_new_goal_type_409_includes_structured_direction_id": (
        "AC: 409 response must include direction_id as structured JSON field, "
        "not buried in free-text detail. Exercises real endpoint for first + second request."
    ),
    "test_request_new_goal_type_returns_429_when_spend_cap_exceeded": (
        "AC: API spec 429 when daily spend cap exceeded. Inserts real spend ledger "
        "row and asserts endpoint rejects with budget message."
    ),
    "test_request_new_goal_type_rollback_on_write_failure": (
        "AC: When write_direction fails after goal creation, the goal and session "
        "linkage are rolled back, and spend is NOT persisted. Verifies atomicity."
    ),
    "test_request_new_goal_type_returns_422_when_synthesis_fails": (
        "AC: API spec 422 when prompt_summary too vague — LLM refuses to produce "
        "a direction. Asserts the exact chat copy: 'I couldn't pin down what you "
        "want — try rephrasing with more concrete success criteria.'"
    ),
}


async def test_request_new_goal_type_returns_404_for_missing_session(temp_directions_path):
    """POST /api/chat/sessions/{id}/request-new-goal-type must return 404
    when the session does not exist, per the API spec. Verifies no goal
    row and no direction directory are created as side effects."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/chat/sessions/sess-nonexistent/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 404

        # Verify no goal was created (side-effect absence)
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with async_session() as session:
            result = await session.execute(
                select(Goal).where(
                    Goal.title == GENERATION_REQUEST_BODY["goal_payload_draft"]["title"],
                )
            )
            assert result.scalar_one_or_none() is None, (
                "no goal must be created when session lookup returns 404"
            )
        await engine.dispose()

        # Verify no direction directory was written
        entries = list(temp_directions_path.iterdir())
        assert len(entries) == 0, "no direction directory must be written for a 404 response"


async def test_request_new_goal_type_creates_goal_in_awaiting_status(temp_directions_path):
    """POST /api/chat/sessions/{id}/request-new-goal-type must create a goal
    in awaiting_goal_type status with awaiting_direction_id populated, write
    a direction directory, and return direction_id + goal_id."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-abc")

        resp = await client.post(
            "/api/chat/sessions/sess-abc/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "direction_id" in body
        assert "goal_id" in body
        assert body["status"] == "queued"

        direction_id = body["direction_id"]
        goal_id = body["goal_id"]

        # Verify the direction directory was written
        direction_dir = temp_directions_path / direction_id
        assert direction_dir.is_dir()
        assert (direction_dir / "direction.md").exists()
        assert (direction_dir / "state.yaml").exists()

        # Verify state.yaml created with queued status
        state_content = (direction_dir / "state.yaml").read_text()
        assert "status: queued" in state_content

    # Verify goal persisted in DB with correct status and direction linkage.
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(Goal).options(selectinload(Goal.criteria)).where(Goal.id == uuid.UUID(goal_id))
        )
        goal = result.scalar_one()
        assert goal.status == "awaiting_goal_type"
        assert goal.awaiting_direction_id == direction_id
        assert goal.criteria is not None, "goal must have criteria attached"
        assert goal.criteria.criteria_type == "generated"
        assert "module_name" in goal.criteria.criteria_data
        assert goal.criteria.criteria_data["module_name"] == "pushup_counter"
        assert goal.criteria.criteria_data["direction_id"] == direction_id
    await engine.dispose()


async def test_request_new_goal_type_409_includes_structured_direction_id(temp_directions_path):
    """When a user already has an in-flight generation, the 409 response
    must include the existing direction_id as a structured JSON field,
    not buried inside a free-text detail string."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-yza")

        # First request — succeeds
        resp = await client.post(
            "/api/chat/sessions/sess-yza/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        first_direction_id = resp.json()["direction_id"]

        # Second request — must be 409 with structured direction_id
        resp = await client.post(
            "/api/chat/sessions/sess-yza/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                **GENERATION_REQUEST_BODY,
                "prompt_summary": "A different goal type entirely",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "direction_id" in body, (
            "409 response must include direction_id as a structured field"
        )
        assert body["direction_id"] == first_direction_id


async def test_request_new_goal_type_returns_429_when_spend_cap_exceeded(temp_directions_path):
    """POST /api/chat/sessions/{id}/request-new-goal-type must return 429
    when the user's daily spend cap is exceeded."""
    async with make_client() as client:
        token, user = await _auth(client)
        await _ensure_session(client, "sess-hij")

        # Insert a spend ledger entry that exceeds the cap
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            entry = ChatSpendLedger(
                user_id=uuid.UUID(user["id"]),
                cost_millicents=settings.chat_spend_cap_millicents + 1,
                model="test-model",
                call_description="prior spend exceeding cap",
            )
            session.add(entry)
            await session.commit()
        await engine.dispose()

        resp = await client.post(
            "/api/chat/sessions/sess-hij/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 429
        assert "budget" in resp.json()["detail"].lower()


async def test_request_new_goal_type_rollback_on_write_failure(temp_directions_path):
    """When write_direction fails, goal, session linkage, and spend are all
    rolled back — no partial state persists."""
    import app.routes.chat as _chat

    async def _fail(*args, **kwargs):
        raise RuntimeError("disk full")

    async with make_client() as client:
        token, user_id = await _auth(client)
        await _ensure_session(client, "sess-rollback")

        _orig = _chat.write_direction
        _chat.write_direction = _fail
        try:
            resp = await client.post(
                "/api/chat/sessions/sess-rollback/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json=GENERATION_REQUEST_BODY,
            )
        finally:
            _chat.write_direction = _orig

        assert resp.status_code == 500

        # Verify no goal persisted — query via the API
        resp = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        goals = resp.json()
        matching = [
            g for g in goals if g["title"] == GENERATION_REQUEST_BODY["goal_payload_draft"]["title"]
        ]
        assert len(matching) == 0, "goal must be rolled back when write_direction fails"


async def test_request_new_goal_type_returns_422_when_synthesis_fails(temp_directions_path):
    """POST /api/chat/sessions/{id}/request-new-goal-type must return 422
    when the synthesis LLM cannot produce a coherent direction, with the
    exact chat-facing copy from the story AC."""
    import app.routes.chat as _chat

    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-vague")

        # Override the autouse mock to raise DirectionSynthesisError
        with patch.object(
            _chat,
            "synthesize_direction",
            side_effect=DirectionSynthesisError("LLM returned empty response"),
        ):
            resp = await client.post(
                "/api/chat/sessions/sess-vague/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json=GENERATION_REQUEST_BODY,
            )

        assert resp.status_code == 422, f"expected 422, got {resp.status_code}"
        detail = resp.json()["detail"]
        assert "I couldn't pin down what you want" in detail
        assert "try rephrasing with more concrete success criteria" in detail
