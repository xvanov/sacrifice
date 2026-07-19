"""Persistence-focused tests for awaiting_goal_type status and direction linkage.

Covers:
- Goal model persists awaiting_goal_type status with nullable awaiting_direction_id
- GET /api/goals/{id} exposes awaiting_direction_id from generation endpoint
- Normal goal creation has null awaiting_direction_id
"""

from datetime import UTC, datetime, timedelta

from app.config import settings
from app.models.goal import Goal
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .utils_goal_generation import (
    GENERATION_REQUEST_BODY,
    VALID_GOAL,
    _auth,
    _ensure_session,
    make_client,
    mock_synthesize_direction,  # noqa: F401 — pytest fixture
)

TEST_PLAN = {
    "test_model_persists_awaiting_goal_type_with_direction_id": (
        "AC: Goal model persists awaiting_goal_type with awaiting_direction_id. "
        "Exercises real ORM insert + re-read to prove both fields survive a commit/read cycle. "
        "Also verifies null awaiting_direction_id persists correctly."
    ),
    "test_goal_get_exposes_awaiting_direction_id_from_generation": (
        "AC: GET /api/goals/{id} must expose awaiting_direction_id populated by "
        "the generation endpoint. Exercises real HTTP endpoint, not manual DB insert."
    ),
    "test_normal_goal_has_null_awaiting_direction_id": (
        "AC: Normal goal via POST /api/goals (not generation) must have "
        "awaiting_direction_id: null. Ensures non-generated goals are not mislabeled."
    ),
}


async def test_model_persists_awaiting_goal_type_with_direction_id():
    """Goal model persists awaiting_goal_type status with awaiting_direction_id
    and correctly re-reads both fields."""
    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="persist@test.com",
            display_name="Persist Tester",
            auth_provider="google",
            auth_provider_id="google-persist-1",
        )
        session.add(user)
        await session.commit()

        goal = Goal(
            user_id=user.id,
            title="Awaiting goal type test",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(UTC) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id="011-pushup-counter",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id
        persisted_user_id = user.id

    # Re-read and verify both status and direction linkage persisted
    async with async_session() as session:
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        persisted = result.scalar_one()
        assert persisted.status == "awaiting_goal_type"
        assert persisted.awaiting_direction_id == "011-pushup-counter"

    # Verify null awaiting_direction_id also persists correctly
    async with async_session() as session:
        goal_null = Goal(
            user_id=persisted_user_id,
            title="Null direction linkage",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(UTC) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id=None,
        )
        session.add(goal_null)
        await session.commit()
        null_id = goal_null.id

    async with async_session() as session:
        result = await session.execute(select(Goal).where(Goal.id == null_id))
        persisted_null = result.scalar_one()
        assert persisted_null.status == "awaiting_goal_type"
        assert persisted_null.awaiting_direction_id is None

    await engine.dispose()


async def test_goal_get_exposes_awaiting_direction_id_from_generation(temp_directions_path):
    """GET /api/goals/{id} must expose awaiting_direction_id populated by the
    generation endpoint, not by manual DB manipulation."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-def")

        # Create through the generation endpoint (the production path)
        resp = await client.post(
            "/api/chat/sessions/sess-def/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        expected_direction_id = resp.json()["direction_id"]

        # GET the goal — awaiting_direction_id must be present
        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["awaiting_direction_id"] == expected_direction_id
        assert body["status"] == "awaiting_goal_type"


async def test_normal_goal_has_null_awaiting_direction_id():
    """Goal created via POST /api/goals (not generation) must have
    awaiting_direction_id: null in the response."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        assert resp.status_code == 201
        goal_id = resp.json()["id"]

        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "awaiting_direction_id" in body
        assert body["awaiting_direction_id"] is None
