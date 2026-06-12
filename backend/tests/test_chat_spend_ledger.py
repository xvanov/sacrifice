"""Tests for chat spend ledger.

These tests assert on production code that does NOT exist yet
(chat_spend_ledger table and service). Every test in this file MUST fail
(RED) on first run against the current codebase.

Covers:
- Per-user per-call cost recording via service layer
- Daily cap enforcement (default $1.00 / 100000 millicents)
- 429 when cap exceeded at the endpoint level
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.services import direction_synth as _direction_synth


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


VALID_GOAL = {
    "title": "20 morning pushups",
    "description": "Do 20 pushups every morning at 7am, verified with my phone camera.",
    "pledge_amount": 1000,
    "currency": "usd",
    "deadline": "2026-05-26T11:00:00Z",
    "timezone": "America/New_York",
    "charity_id": "acct_charity123",
    "recurrence": "daily",
}


# ─── Service-layer: record_spend and daily cap ─────────────────────────


async def test_record_spend_persists_ledger_entry():
    """record_spend must insert a row into chat_spend_ledger."""
    from app.services.chat_spend import record_spend

    user_id = uuid.uuid4()
    direction_id = "022-spend-test"

    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with sf() as db:
        # Create user first
        await db.execute(
            text("""
                INSERT INTO users (id, email, display_name, auth_provider, auth_provider_id, created_at)
                VALUES (:id, :email, :name, :provider, :pid, now())
            """),
            {
                "id": user_id,
                "email": "spendtest@example.com",
                "name": "Spend Test",
                "provider": "google",
                "pid": "google-spendtest-1",
            },
        )
        await db.commit()

        entry = await record_spend(
            db=db,
            user_id=user_id,
            call_type="direction_synthesis",
            model="gpt-4o-mini",
            millicents=1500,
            direction_id=direction_id,
        )

    assert entry is not None
    assert entry.id is not None
    assert entry.user_id == user_id
    assert entry.model == "gpt-4o-mini"
    assert entry.cost_millicents == 1500
    assert direction_id in entry.call_description

    await engine.dispose()


async def test_daily_spend_cap_default_is_one_dollar():
    """Daily spend cap must default to $1.00 (100000 millicents)."""
    from app.services.chat_spend import DEFAULT_DAILY_CAP_MILLICENTS

    assert DEFAULT_DAILY_CAP_MILLICENTS == 100000, (
        f"Expected 100000 millicents ($1.00), got {DEFAULT_DAILY_CAP_MILLICENTS}"
    )


async def test_daily_spend_query_sums_today_only():
    """Daily spend check must only sum calls from the current UTC day."""
    from app.services.chat_spend import get_daily_spend, record_spend

    user_id = uuid.uuid4()

    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with sf() as db:
        await db.execute(
            text("""
                INSERT INTO users (id, email, display_name, auth_provider, auth_provider_id, created_at)
                VALUES (:id, :email, :name, :provider, :pid, now())
            """),
            {
                "id": user_id,
                "email": "dailyspend@example.com",
                "name": "Daily Spend",
                "provider": "google",
                "pid": "google-dailyspend-1",
            },
        )
        await db.commit()

        # Insert a yesterday record via raw SQL
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        await db.execute(
            text("""
                INSERT INTO chat_spend_ledger
                    (id, user_id, model, cost_millicents, call_description, call_timestamp)
                VALUES (:id, :user_id, :model, :cost_millicents, :call_description, :call_timestamp)
            """),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "model": "test",
                "cost_millicents": 90000,
                "call_description": "direction_synthesis:old-direction",
                "call_timestamp": yesterday,
            },
        )
        await db.commit()

        # Insert a today record via the service
        await record_spend(
            db=db,
            user_id=user_id,
            call_type="direction_synthesis",
            model="test",
            millicents=5000,
            direction_id="today-direction",
        )

        # Call get_daily_spend through the service
        today_total = await get_daily_spend(db, user_id)
        assert today_total == 5000, f"Expected 5000, got {today_total} — yesterday's 90000 should be excluded"

    await engine.dispose()


async def test_check_daily_spend_cap_returns_false_when_cap_exceeded():
    """check_daily_spend_cap must return False only when cap would be exceeded.

    The API contract allows requests up to (and including) the cap;
    only requests that would push spend OVER the cap must be rejected.
    """
    from app.services.chat_spend import get_daily_spend, check_daily_cap

    user_id = uuid.uuid4()

    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with sf() as db:
        await db.execute(
            text("""
                INSERT INTO users (id, email, display_name, auth_provider, auth_provider_id, created_at)
                VALUES (:id, :email, :name, :provider, :pid, now())
            """),
            {
                "id": user_id,
                "email": "capexceed@example.com",
                "name": "Cap Exceed",
                "provider": "google",
                "pid": "google-capexceed-1",
            },
        )
        await db.commit()

        # Seed spend to cap - 1 millicent. With estimated cost of 200,
        # this would put us at cap+199 which exceeds, so must return False.
        await db.execute(
            text("""
                INSERT INTO chat_spend_ledger
                    (id, user_id, model, cost_millicents, call_description, call_timestamp)
                VALUES (:id, :user_id, :model, :cost_millicents, :call_description, now())
            """),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "model": "test",
                "cost_millicents": settings.chat_daily_spend_cap_millicents - 1,
                "call_description": "direction_synthesis:cap-edge",
            },
        )
        await db.commit()

        # At-the-cap spending is allowed (<= guard).
        under_cap_at = await check_daily_cap(db, user_id, estimated_cost_millicents=1)
        assert under_cap_at is True, "check_daily_cap must return True when spend would equal cap exactly"

        # Over the cap must be rejected.
        under_cap_over = await check_daily_cap(db, user_id, estimated_cost_millicents=2)
        assert under_cap_over is False, "check_daily_cap must return False when spend would exceed cap"

    await engine.dispose()


# ─── Endpoint-level: 429 when cap exceeded ────────────────────────────


async def test_request_new_goal_type_returns_429_when_daily_cap_exceeded(tmp_path):
    """Endpoint must return 429 when user's daily AI budget is exhausted."""
    session_id = str(uuid.uuid4())

    async with make_client() as client:
        token, user = await _auth(client)

        # Seed session so the endpoint finds it
        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         session_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :sid, :cid, now(), now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "title": "Session seed",
                    "desc": "",
                    "gtype": "youtube_video",
                    "amt": 0,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "draft",
                    "sid": session_id,
                    "cid": None,
                },
            )
            await db.commit()

        # Seed spend ledger to the cap
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO chat_spend_ledger
                        (id, user_id, model, cost_millicents, call_description, call_timestamp)
                    VALUES
                        (:id, :user_id, :model, :cost_millicents, :call_description, now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "model": settings.direction_synth_model,
                    "cost_millicents": settings.chat_daily_spend_cap_millicents,
                    "call_description": "direction_synthesis:old-direction",
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                    "goal_payload_draft": VALID_GOAL,
                },
            )

        assert resp.status_code == 429
        body = resp.json()
        detail = body.get("detail", "")
        assert "budget" in detail.lower() or "spend" in detail.lower() or "cap" in detail.lower()

        # Assert no goal was created
        engine2 = create_async_engine(settings.database_url, echo=False)
        sf2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with sf2() as db:
            result = await db.execute(
                text("SELECT COUNT(*) FROM goals WHERE user_id = :uid AND status = 'awaiting_goal_type'"),
                {"uid": user["id"]},
            )
            assert result.scalar() == 0
        await engine2.dispose()