import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models.goal import Goal
from app.config import settings


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


async def _query_goal(goal_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(select(Goal).where(Goal.id == goal_id))
        goal = result.scalar_one_or_none()
    await engine.dispose()
    return goal


# --- Story D010 AC: Deadline worker skips awaiting_goal_type goals ---


async def test_awaiting_goal_type_goal_not_charged_or_failed_by_deadline_worker():
    """An awaiting_goal_type goal past its deadline must NOT be transitioned
    to failed and must NOT trigger a charge — it is not yet active."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)

        # Create goal via API then force its status to awaiting_goal_type
        # via raw SQL (bypasses SQLAlchemy enum — will fail pre-impl
        # because the PG enum doesn't include awaiting_goal_type yet).
        deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Pushup Counter Goal",
                "description": "Do 20 pushups every morning at 7am verified with phone camera",
                "deadline": deadline,
                "pledge_amount": 1000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 60, "video_description": "pushups"},
                "charity_id": "acct_charity_123",
            },
        )
        goal_id = resp.json()["id"]
        goal_uuid = uuid.UUID(goal_id)

        # Force status to awaiting_goal_type via raw SQL
        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as db:
            await db.execute(
                text("UPDATE goals SET status = :status WHERE id = :id"),
                {"status": "awaiting_goal_type", "id": goal_uuid},
            )
            await db.commit()
        await engine.dispose()

        # Verify the status was set
        goal = await _query_goal(goal_id)
        assert goal.status == "awaiting_goal_type"

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        # Goal must remain awaiting_goal_type — not failed
        goal = await _query_goal(goal_id)
        assert goal.status == "awaiting_goal_type", \
            f"Expected awaiting_goal_type, got {goal.status}"

        # Charge must NOT have been called
        mock_charge.assert_not_called()


async def test_active_goals_still_processed_when_awaiting_goal_type_exists():
    """An active goal past deadline must still be charged even when an
    awaiting_goal_type goal exists in the database."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)

        # Create an active goal past deadline
        active_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Active Goal To Be Charged",
                "description": "This one should be enforced",
                "deadline": active_deadline,
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "test"},
                "charity_id": "acct_charity_123",
            },
        )
        active_goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{active_goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        # Create a second goal and force it to awaiting_goal_type
        gen_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Generated Pushup Verifier",
                "description": "Awaiting goal type generation",
                "deadline": gen_deadline,
                "pledge_amount": 1000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 60, "video_description": "pushups"},
                "charity_id": "acct_charity_123",
            },
        )
        gen_goal_id = resp.json()["id"]
        gen_goal_uuid = uuid.UUID(gen_goal_id)

        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as db:
            await db.execute(
                text("UPDATE goals SET status = :status WHERE id = :id"),
                {"status": "awaiting_goal_type", "id": gen_goal_uuid},
            )
            await db.commit()
        await engine.dispose()

        gen_goal = await _query_goal(gen_goal_id)
        assert gen_goal.status == "awaiting_goal_type"

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        # Active goal must be processed (failed + charged)
        active_goal = await _query_goal(active_goal_id)
        assert active_goal.status == "failed", \
            f"Active goal should be failed, got {active_goal.status}"

        # awaiting_goal_type goal must remain untouched
        gen_goal = await _query_goal(gen_goal_id)
        assert gen_goal.status == "awaiting_goal_type", \
            f"Generated goal should stay awaiting_goal_type, got {gen_goal.status}"

        # Charge must have been called exactly once (for the active goal only)
        assert mock_charge.call_count == 1, \
            f"Expected 1 charge call (active goal only), got {mock_charge.call_count}"