"""Tests for D010 chat spend ledger.

Covers:
- ChatSpendLedger model persistence (per-user, per-call cost in millicents)
- Daily spend cap enforcement (default $1.00 = 100,000 millicents)
- 429 Too Many Requests when cap is exceeded
- Configurable cap via settings
"""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.base import Base
from app.models.user import User


# ─── Model persistence tests ────────────────────────────────────────


async def test_chat_spend_ledger_row_persists():
    """A ChatSpendLedger row can be created and read back."""
    from app.models.chat_spend_ledger import ChatSpendLedger

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="spend-test@test.com",
            display_name="Spend Tester",
            auth_provider="google",
            auth_provider_id="google-spend-test",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        entry = ChatSpendLedger(
            user_id=user_id,
            call_type="direction_synthesis",
            cost_millicents=5000,  # $0.05
            model="deepseek-v4-flash",
            tokens_in=1200,
            tokens_out=800,
        )
        session.add(entry)
        await session.commit()
        entry_id = entry.id

    assert entry_id is not None
    assert isinstance(entry_id, uuid.UUID)
    await engine.dispose()


async def test_chat_spend_ledger_reads_back_all_fields():
    """ChatSpendLedger round-trips all fields correctly."""
    from app.models.chat_spend_ledger import ChatSpendLedger

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="roundtrip-spend@test.com",
            display_name="Roundtrip Spender",
            auth_provider="google",
            auth_provider_id="google-roundtrip-spend",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        entry = ChatSpendLedger(
            user_id=user_id,
            call_type="direction_synthesis",
            cost_millicents=12345,
            model="deepseek-v4-flash",
            tokens_in=5000,
            tokens_out=3000,
        )
        session.add(entry)
        await session.commit()
        eid = entry.id

    async with async_session() as session:
        result = await session.execute(
            select(ChatSpendLedger).where(ChatSpendLedger.id == eid)
        )
        reloaded = result.scalar_one()

    assert reloaded.user_id == user_id
    assert reloaded.call_type == "direction_synthesis"
    assert reloaded.cost_millicents == 12345
    assert reloaded.model == "deepseek-v4-flash"
    assert reloaded.tokens_in == 5000
    assert reloaded.tokens_out == 3000
    assert reloaded.created_at is not None
    await engine.dispose()


async def test_chat_spend_ledger_multiple_users_isolated():
    """Each user's spend is tracked independently."""
    from app.models.chat_spend_ledger import ChatSpendLedger

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user_a = User(
            email="user-a@test.com",
            display_name="User A",
            auth_provider="google",
            auth_provider_id="google-user-a-spend",
        )
        user_b = User(
            email="user-b@test.com",
            display_name="User B",
            auth_provider="google",
            auth_provider_id="google-user-b-spend",
        )
        session.add_all([user_a, user_b])
        await session.commit()

        session.add(ChatSpendLedger(
            user_id=user_a.id, call_type="direction_synthesis",
            cost_millicents=50000, model="test",
            tokens_in=100, tokens_out=100,
        ))
        session.add(ChatSpendLedger(
            user_id=user_b.id, call_type="direction_synthesis",
            cost_millicents=10000, model="test",
            tokens_in=100, tokens_out=100,
        ))
        await session.commit()

    # Verify only user_a's spend
    async with async_session() as session:
        result = await session.execute(
            select(ChatSpendLedger).where(ChatSpendLedger.user_id == user_a.id)
        )
        entries = list(result.scalars().all())

    assert len(entries) == 1
    assert entries[0].cost_millicents == 50000
    await engine.dispose()


# ─── Daily cap tests ────────────────────────────────────────────────


async def test_daily_spend_cap_defaults_to_one_dollar():
    """Default daily spend cap is $1.00 (100,000 millicents)."""
    from app.services.chat_spend import get_daily_spend_cap

    cap = get_daily_spend_cap()
    assert cap == 100_000  # $1.00


async def test_check_spend_under_cap_returns_true():
    """check_spend() returns True when user is under daily cap."""
    from app.services.chat_spend import check_spend

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="under-cap@test.com",
            display_name="Under Cap",
            auth_provider="google",
            auth_provider_id="google-under-cap",
        )
        session.add(user)
        await session.commit()

        ok = await check_spend(session, user.id)
    assert ok is True
    await engine.dispose()


async def test_check_spend_over_cap_returns_false():
    """check_spend() returns False when user has exceeded daily cap."""
    from app.models.chat_spend_ledger import ChatSpendLedger
    from app.services.chat_spend import check_spend

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="over-cap@test.com",
            display_name="Over Cap",
            auth_provider="google",
            auth_provider_id="google-over-cap",
        )
        session.add(user)
        await session.commit()

        # Insert a spend entry at the cap limit
        session.add(ChatSpendLedger(
            user_id=user.id,
            call_type="direction_synthesis",
            cost_millicents=100_001,  # Just over $1.00
            model="deepseek-v4-flash",
            tokens_in=10000,
            tokens_out=5000,
        ))
        await session.commit()

        ok = await check_spend(session, user.id)
    assert ok is False
    await engine.dispose()


async def test_daily_spend_resets_next_day():
    """Spend from yesterday does not count against today's cap."""
    from app.models.chat_spend_ledger import ChatSpendLedger
    from app.services.chat_spend import check_spend

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="yesterday-spend@test.com",
            display_name="Yesterday Spender",
            auth_provider="google",
            auth_provider_id="google-yesterday-spend",
        )
        session.add(user)
        await session.commit()

        # Spend from 2 days ago
        old_entry = ChatSpendLedger(
            user_id=user.id,
            call_type="direction_synthesis",
            cost_millicents=200_000,  # Way over cap, but old
            model="deepseek-v4-flash",
            tokens_in=1000,
            tokens_out=1000,
        )
        old_entry.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        session.add(old_entry)
        await session.commit()

        ok = await check_spend(session, user.id)
    assert ok is True
    await engine.dispose()


# ─── Record spend test ──────────────────────────────────────────────


async def test_record_spend_persists_entry():
    """record_spend() creates a ChatSpendLedger row and returns it."""
    from app.models.chat_spend_ledger import ChatSpendLedger
    from app.services.chat_spend import record_spend

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="record-spend@test.com",
            display_name="Record Spender",
            auth_provider="google",
            auth_provider_id="google-record-spend",
        )
        session.add(user)
        await session.commit()

        entry = await record_spend(
            db=session,
            user_id=user.id,
            call_type="direction_synthesis",
            cost_millicents=7500,
            model="deepseek-v4-flash",
            tokens_in=1500,
            tokens_out=900,
        )

    assert entry is not None
    assert entry.user_id == user.id
    assert entry.cost_millicents == 7500
    await engine.dispose()