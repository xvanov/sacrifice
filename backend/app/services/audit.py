"""Audit event service — minimal persistence path for proof validation outcomes.

Every proof submission attempt (accepted or rejected) and every illegal
transition is recorded so the system can account for all submission activity.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent


async def create_audit_event(
    db: AsyncSession,
    *,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    event_type: str,
    details: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        goal_id=goal_id,
        user_id=user_id,
        event_type=event_type,
        details=details or {},
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event