"""Tests for chat spend ledger.

These tests assert on production code that does NOT exist yet
(chat_spend_ledger table and service). Every test in this file MUST fail
(RED) on first run against the current codebase.

Covers:
- chat_spend_ledger table exists and accepts records
- Per-user per-call cost recording
- Daily cap enforcement (default $1.00 / 100000 millicents)
- 429 when cap exceeded
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


# ─── Model-layer: chat_spend_ledger table ─────────────────────────


async def test_chat_spend_ledger_table_accepts_record():
    """chat_spend_ledger table must exist and accept a spend record."""
    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="spendledger@test.com",
            display_name="Spend Ledger Tester",
            auth_provider="google",
            auth_provider_id="google-spend-1",
        )
        session.add(user)
        await session.commit()

        # Direct SQL insert — table may not exist yet, so this will fail RED
        await session.execute(
            text("""
                INSERT INTO chat_spend_ledger
                    (id, user_id, direction_id, call_type, model, millicents, created_at)
                VALUES
                    (:id, :user_id, :direction_id, :call_type, :model, :millicents, :created_at)
            """),
            {
                "id": uuid.uuid4(),
                "user_id": user.id,
                "direction_id": "011-pushup-counter",
                "call_type": "direction_synthesis",
                "model": "gpt-4o-mini",
                "millicents": 1500,
                "created_at": datetime.now(timezone.utc),
            },
        )
        await session.commit()

        result = await session.execute(text("SELECT COUNT(*) FROM chat_spend_ledger"))
        count = result.scalar()
        assert count == 1

    await engine.dispose()


async def test_chat_spend_ledger_has_required_columns():
    """chat_spend_ledger must have user_id, direction_id, call_type, model, millicents, created_at."""
    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'chat_spend_ledger'
                ORDER BY ordinal_position
            """)
        )
        columns = {row[0] for row in result}

    required = {"id", "user_id", "direction_id", "call_type", "model", "millicents", "created_at"}
    missing = required - columns
    assert not missing, f"Missing columns in chat_spend_ledger: {missing}"

    await engine.dispose()


# ─── Daily cap enforcement ─────────────────────────────────────────


async def test_daily_spend_cap_default_is_one_dollar():
    """Daily spend cap must default to $1.00 (100000 millicents)."""
    from app.services.chat_spend import DEFAULT_DAILY_CAP_MILLICENTS

    # The service must expose a constant for the default cap
    assert DEFAULT_DAILY_CAP_MILLICENTS == 100000, (
        f"Expected 100000 millicents ($1.00), got {DEFAULT_DAILY_CAP_MILLICENTS}"
    )


async def test_daily_spend_query_sums_today_only():
    """Daily spend check must only sum calls from the current UTC day."""
    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="dailycap@test.com",
            display_name="Daily Cap Tester",
            auth_provider="google",
            auth_provider_id="google-dailycap-1",
        )
        session.add(user)
        await session.commit()

        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)

        # Insert a yesterday record
        await session.execute(
            text("""
                INSERT INTO chat_spend_ledger
                    (id, user_id, direction_id, call_type, model, millicents, created_at)
                VALUES (:id, :user_id, :direction_id, :call_type, :model, :millicents, :created_at)
            """),
            {
                "id": uuid.uuid4(),
                "user_id": user.id,
                "direction_id": "old-direction",
                "call_type": "direction_synthesis",
                "model": "test",
                "millicents": 90000,
                "created_at": yesterday,
            },
        )

        # Insert a today record
        await session.execute(
            text("""
                INSERT INTO chat_spend_ledger
                    (id, user_id, direction_id, call_type, model, millicents, created_at)
                VALUES (:id, :user_id, :direction_id, :call_type, :model, :millicents, :created_at)
            """),
            {
                "id": uuid.uuid4(),
                "user_id": user.id,
                "direction_id": "today-direction",
                "call_type": "direction_synthesis",
                "model": "test",
                "millicents": 5000,
                "created_at": today,
            },
        )
        await session.commit()

        # Sum today only
        start_of_today = today.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await session.execute(
            text("""
                SELECT COALESCE(SUM(millicents), 0)
                FROM chat_spend_ledger
                WHERE user_id = :user_id AND created_at >= :start_of_today
            """),
            {"user_id": user.id, "start_of_today": start_of_today},
        )
        today_total = result.scalar()

        # The yesterday spend should NOT be counted
        assert today_total == 5000

    await engine.dispose()


async def test_chat_returns_429_when_daily_cap_exceeded():
    """Chat endpoint must return 429 when user's daily spend cap is exceeded."""
    session_id = str(uuid.uuid4())

    async with make_client() as client:
        token, _ = await _auth(client)

        # Patch the spend checker to simulate cap exceeded
        with patch(
            "app.routes.chat.check_daily_spend_cap", new_callable=AsyncMock
        ) as mock_check:
            mock_check.side_effect = ValueError("spend_cap:exceeded")

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "Do 20 pushups every morning",
                    "goal_payload_draft": {
                        "title": "Test",
                        "description": "Test",
                        "pledge_amount": 1000,
                        "currency": "usd",
                        "deadline": "2026-05-26T11:00:00Z",
                        "timezone": "UTC",
                        "charity_id": "acct_test",
                        "recurrence": "daily",
                    },
                },
            )

        assert resp.status_code == 429
        body = resp.json()
        detail = body.get("detail", str(body))
        assert "budget" in detail.lower() or "spend" in detail.lower() or "cap" in detail.lower()