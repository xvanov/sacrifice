"""Chat spend tracking — per-user per-day spend cap enforcement."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chat_spend_ledger import ChatSpendLedger


async def check_spend_cap(
    user_id: str,
    db: AsyncSession,
) -> bool:
    """Return True if the user is under their daily spend cap.

    Query the ledger for today's total spend in millicents, compare against
    the configured cap (`chat_daily_spend_cap_millicents`, default $1.00).
    """
    cap = settings.chat_daily_spend_cap_millicents

    # Today's start in UTC
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(func.coalesce(func.sum(ChatSpendLedger.millicents), 0))
        .where(
            ChatSpendLedger.user_id == user_id,
            ChatSpendLedger.created_at >= today_start,
        )
    )
    spent_today = result.scalar() or 0

    return spent_today < cap


async def record_spend(
    user_id: str,
    millicents: int,
    call_type: str,
    model: str,
    direction_id: str | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Record an LLM call in the spend ledger."""
    if db is None:
        return

    entry = ChatSpendLedger(
        user_id=user_id,
        direction_id=direction_id,
        call_type=call_type,
        model=model,
        millicents=millicents,
    )
    db.add(entry)
    await db.commit()