"""
D010 — chat spend ledger and per-user daily cap enforcement.

Tests that:
- A ChatSpendLedger model can persist per-user, per-call millicent costs.
- The default daily cap is $1.00 (100,000 millicents).
- Preflight cap check allows calls under cap.
- Preflight cap check rejects at exact-cap boundary (cap exhausted = 429).
- Preflight cap check rejects over-cap calls with 429.
- Per-user isolation: user A's spend does not affect user B's cap.
- Per-day isolation: yesterday's spend does not affect today's cap.
- The chat endpoints return 429 when cap is exceeded.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app
from app.models.user import User


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ─── Helpers ────────────────────────────────────────────────────────


async def _create_user(db: AsyncSession, email: str, display_name: str = "Test") -> User:
    user = User(
        email=email,
        display_name=display_name,
        auth_provider="google",
        auth_provider_id=f"sub-{email}",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _insert_ledger_rows(db: AsyncSession, user_id: uuid.UUID, cost_millicents: int, count: int = 1):
    """Raw INSERT into chat_spend_ledger — used to seed spend before the model exists."""
    for _ in range(count):
        await db.execute(
            text(
                "INSERT INTO chat_spend_ledger (id, user_id, cost_millicents, call_type, created_at) "
                "VALUES (gen_random_uuid(), :uid, :cost, 'generation', NOW())"
            ),
            {"uid": user_id, "cost": cost_millicents},
        )
    await db.commit()


# ─── Config ─────────────────────────────────────────────────────────


class TestDefaultDailyCap:
    def test_default_daily_cap_is_one_dollar_in_millicents(self):
        """D010 AC: configurable cap per user per day, default $1.00."""
        assert hasattr(settings, "chat_daily_cap_millicents"), (
            "chat_daily_cap_millicents field must exist on Settings"
        )
        cap = settings.chat_daily_cap_millicents
        assert cap == 100_000, (
            f"Expected default daily cap 100_000 millicents ($1.00), got {cap}"
        )

    def test_chat_daily_cap_is_configurable_via_env(self):
        """The cap must be read from settings so operators can tune it."""
        assert hasattr(settings, "chat_daily_cap_millicents"), (
            "chat_daily_cap_millicents must exist as a Settings field"
        )
        field_info = settings.model_fields.get("chat_daily_cap_millicents")
        assert field_info is not None, "chat_daily_cap_millicents must be a declared field"


# ─── Model ──────────────────────────────────────────────────────────


class TestChatSpendLedgerModel:
    async def test_create_ledger_entry_persists(self, test_db):
        """A ChatSpendLedger row can be inserted and read back."""
        from app.database import get_db

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            user = await _create_user(session, f"ledger-{uuid.uuid4().hex[:8]}@test.com")
            await session.execute(
                text(
                    "INSERT INTO chat_spend_ledger (id, user_id, cost_millicents, call_type, created_at) "
                    "VALUES (gen_random_uuid(), :uid, 50000, 'generation', NOW())"
                ),
                {"uid": user.id},
            )
            await session.commit()

            result = await session.execute(
                text("SELECT user_id, cost_millicents, call_type FROM chat_spend_ledger WHERE user_id = :uid"),
                {"uid": user.id},
            )
            rows = result.fetchall()
            assert len(rows) == 1
            assert rows[0].cost_millicents == 50000
            assert rows[0].call_type == "generation"
        finally:
            await session.close()

    async def test_ledger_records_multiple_calls(self, test_db):
        """Multiple LLM calls for the same user each get their own row."""
        from app.database import get_db

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            user = await _create_user(session, f"multi-{uuid.uuid4().hex[:8]}@test.com")
            await _insert_ledger_rows(session, user.id, 10000, count=3)

            result = await session.execute(
                text("SELECT COUNT(*) as cnt FROM chat_spend_ledger WHERE user_id = :uid"),
                {"uid": user.id},
            )
            assert result.scalar() == 3
        finally:
            await session.close()


# ─── Spend cap service ──────────────────────────────────────────────


class TestPreflightCapCheck:
    """D010 AC: under-cap success, exact-cap boundary, over-cap rejection."""

    async def test_under_cap_allows_call(self, test_db):
        """Spend below daily cap must not block."""
        from app.database import get_db
        from app.services.spend_ledger import check_daily_cap

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            user = await _create_user(session, f"under-{uuid.uuid4().hex[:8]}@test.com")
            cap = settings.chat_daily_cap_millicents

            await _insert_ledger_rows(session, user.id, 50000)

            allowed, detail = await check_daily_cap(session, user.id, cap)
            assert allowed is True, f"Expected under-cap to be allowed, got: {detail}"
            assert detail == ""
        finally:
            await session.close()

    async def test_exact_cap_boundary_blocks_call(self, test_db):
        """When spend equals the daily cap, further calls must be blocked (429)."""
        from app.database import get_db
        from app.services.spend_ledger import check_daily_cap

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            user = await _create_user(session, f"exact-{uuid.uuid4().hex[:8]}@test.com")
            cap = settings.chat_daily_cap_millicents

            await _insert_ledger_rows(session, user.id, cap)

            allowed, detail = await check_daily_cap(session, user.id, cap)
            assert allowed is False, "Expected exact-cap to be blocked"
            assert "budget" in detail.lower() or "cap" in detail.lower() or "limit" in detail.lower(), (
                f"Expected clear message about budget/cap, got: {detail}"
            )
        finally:
            await session.close()

    async def test_over_cap_blocks_call_with_clear_message(self, test_db):
        """When spend exceeds daily cap, calls must be blocked with a clear message."""
        from app.database import get_db
        from app.services.spend_ledger import check_daily_cap

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            user = await _create_user(session, f"over-{uuid.uuid4().hex[:8]}@test.com")
            cap = settings.chat_daily_cap_millicents

            await _insert_ledger_rows(session, user.id, cap + 1)

            allowed, detail = await check_daily_cap(session, user.id, cap)
            assert allowed is False, "Expected over-cap to be blocked"
            assert len(detail) > 0, "Rejection must include a human-readable message"
        finally:
            await session.close()


class TestSpendRecording:
    async def test_record_spend_persists_cost(self, test_db):
        """record_spend() must write a new row to chat_spend_ledger."""
        from app.database import get_db
        from app.services.spend_ledger import record_spend

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            user = await _create_user(session, f"record-{uuid.uuid4().hex[:8]}@test.com")
            await record_spend(session, user.id, 25000, "generation")

            result = await session.execute(
                text("SELECT cost_millicents, call_type FROM chat_spend_ledger WHERE user_id = :uid"),
                {"uid": user.id},
            )
            rows = result.fetchall()
            assert len(rows) == 1
            assert rows[0].cost_millicents == 25000
            assert rows[0].call_type == "generation"
        finally:
            await session.close()


# ─── Isolation ──────────────────────────────────────────────────────


class TestPerUserIsolation:
    """D010 AC: per-user isolation — user A's spend must not affect user B's cap."""

    async def test_user_a_spend_does_not_block_user_b(self, test_db):
        from app.database import get_db
        from app.services.spend_ledger import check_daily_cap

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            cap = settings.chat_daily_cap_millicents

            user_a = await _create_user(session, f"isol-a-{uuid.uuid4().hex[:8]}@test.com")
            user_b = await _create_user(session, f"isol-b-{uuid.uuid4().hex[:8]}@test.com")

            await _insert_ledger_rows(session, user_a.id, cap)

            allowed_b, detail_b = await check_daily_cap(session, user_b.id, cap)
            assert allowed_b is True, (
                f"User B should be unaffected by User A's spend, got: {detail_b}"
            )

            allowed_a, _ = await check_daily_cap(session, user_a.id, cap)
            assert allowed_a is False, "User A should be blocked after exhausting cap"
        finally:
            await session.close()


class TestPerDayIsolation:
    """D010 AC: per-day isolation — yesterday's spend must not affect today's cap."""

    async def test_yesterday_spend_does_not_block_today(self, test_db):
        from app.database import get_db
        from app.services.spend_ledger import check_daily_cap

        db_gen = app.dependency_overrides[get_db]()
        session = await anext(db_gen)

        try:
            cap = settings.chat_daily_cap_millicents
            user = await _create_user(session, f"day-{uuid.uuid4().hex[:8]}@test.com")

            yesterday = datetime.now(timezone.utc) - timedelta(days=1)

            await session.execute(
                text(
                    "INSERT INTO chat_spend_ledger (id, user_id, cost_millicents, call_type, created_at) "
                    "VALUES (gen_random_uuid(), :uid, :cost, 'generation', :ts)"
                ),
                {"uid": user.id, "cost": cap, "ts": yesterday},
            )
            await session.commit()

            allowed, detail = await check_daily_cap(session, user.id, cap)
            assert allowed is True, (
                f"Yesterday's spend should not affect today's cap, got: {detail}"
            )
        finally:
            await session.close()


# ─── HTTP endpoint tests ────────────────────────────────────────────


class TestRequestNewGoalTypeSpendCap:
    """POST /api/chat/sessions/{session_id}/request-new-goal-type enforces spend cap."""

    async def test_request_new_goal_type_under_cap_returns_202(self):
        """Under cap: endpoint returns 202."""
        async with make_client() as client:
            resp = await client.post(
                "/api/chat/sessions/fake-session-id/request-new-goal-type",
                json={
                    "prompt_summary": "Do 20 pushups every morning",
                    "goal_payload_draft": {
                        "title": "20 morning pushups",
                        "description": "Test",
                        "pledge_amount": 1000,
                        "currency": "usd",
                        "deadline": "2026-05-26T11:00:00Z",
                        "timezone": "America/New_York",
                        "charity_id": "cs_test",
                        "recurrence": "daily",
                    },
                },
            )
        assert resp.status_code == 202, (
            f"Expected 202, got {resp.status_code}: {resp.text}"
        )

    async def test_request_new_goal_type_over_cap_returns_429(self):
        """Over cap: endpoint returns 429 with a clear message."""
        async with make_client() as client:
            resp = await client.post(
                "/api/chat/sessions/fake-session-id/request-new-goal-type",
                json={
                    "prompt_summary": "Do 20 pushups every morning",
                    "goal_payload_draft": {
                        "title": "20 morning pushups",
                        "description": "Test",
                        "pledge_amount": 1000,
                        "currency": "usd",
                        "deadline": "2026-05-26T11:00:00Z",
                        "timezone": "America/New_York",
                        "charity_id": "cs_test",
                        "recurrence": "daily",
                    },
                },
            )
        assert resp.status_code == 429, (
            f"Expected 429, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "detail" in body
        assert (
            "budget" in body["detail"].lower()
            or "cap" in body["detail"].lower()
            or "limit" in body["detail"].lower()
        ), f"429 message must mention budget/cap/limit, got: {body['detail']}"


class TestIterateGeneratedTypeSpendCap:
    """POST /api/chat/sessions/{session_id}/iterate-generated-type enforces spend cap."""

    async def test_iterate_generated_type_over_cap_returns_429(self):
        """Over cap: iterate endpoint returns 429."""
        async with make_client() as client:
            resp = await client.post(
                "/api/chat/sessions/fake-session-id/iterate-generated-type",
                json={"feedback": "Use a side-on camera angle"},
            )
        assert resp.status_code == 429, (
            f"Expected 429, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "detail" in body


# ─── Unauthenticated access ─────────────────────────────────────────


class TestChatEndpointsRequireAuth:
    async def test_request_new_goal_type_without_token_returns_401(self):
        async with make_client() as client:
            resp = await client.post(
                "/api/chat/sessions/fake-session-id/request-new-goal-type",
                json={"prompt_summary": "test", "goal_payload_draft": {}},
            )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}"
        )

    async def test_iterate_generated_type_without_token_returns_401(self):
        async with make_client() as client:
            resp = await client.post(
                "/api/chat/sessions/fake-session-id/iterate-generated-type",
                json={"feedback": "test"},
            )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}"
        )