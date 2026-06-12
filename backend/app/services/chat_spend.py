"""Chat spend ledger — per-user, per-call cost tracking with daily cap."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chat_spend import ChatSpendLedger

# Default daily cap: $1.00 = 100,000 millicents
DEFAULT_DAILY_CAP_MILLICENTS = 100_000


async def record_spend(
    db: AsyncSession,
    user_id: uuid.UUID,
    call_type: str,
    model: str,
    millicents: int,
    direction_id: str | None = None,
) -> ChatSpendLedger:
    entry = ChatSpendLedger(
        user_id=user_id,
        model=model,
        cost_millicents=millicents,
        call_description=(
            f"{call_type}:{direction_id}" if direction_id
            else call_type
        )[:255],
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def get_daily_spend(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Return total millicents spent today by this user."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.coalesce(func.sum(ChatSpendLedger.cost_millicents), 0)).where(
            ChatSpendLedger.user_id == user_id,
            ChatSpendLedger.call_timestamp >= today,
        )
    )
    return result.scalar() or 0


async def check_daily_cap(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Return True if the user is under their daily spend cap."""
    spent = await get_daily_spend(db, user_id)
    return spent < settings.chat_daily_spend_cap_millicents


# Alias for test patching compatibility
check_daily_spend_cap = check_daily_cap


async def has_in_flight_generation(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: str | None = None,
) -> str | None:
    """Return the direction_id if the user has an in-flight generation, else None.

    If session_id is provided, only check for in-flight generations scoped
    to that session. Otherwise check globally for the user.
    """
    if session_id:
        result = await db.execute(
            text(
                "SELECT awaiting_direction_id FROM goals "
                "WHERE user_id = :uid AND status = :status AND session_id = :sid "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"uid": user_id, "status": "awaiting_goal_type", "sid": session_id},
        )
    else:
        result = await db.execute(
            text(
                "SELECT awaiting_direction_id FROM goals "
                "WHERE user_id = :uid AND status = :status "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"uid": user_id, "status": "awaiting_goal_type"},
        )
    row = result.one_or_none()
    if row and row[0]:
        return row[0]
    return None