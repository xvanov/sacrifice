import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def create_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str | None = None,
    goal_id: uuid.UUID | None = None,
) -> Notification:
    notif = Notification(
        user_id=user_id,
        goal_id=goal_id,
        type=notification_type,
        title=title,
        body=body,
        read=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)
    return notif


async def get_user_notifications(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
) -> list[Notification]:
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_unread_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.read == False,
        )
    )
    return len(list(result.scalars().all()))


async def mark_notification_read(
    db: AsyncSession, user_id: uuid.UUID, notification_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        return False
    notif.read = True
    await db.commit()
    return True


async def mark_all_notifications_read(
    db: AsyncSession, user_id: uuid.UUID
) -> int:
    result = await db.execute(
        text("""
            UPDATE notifications SET read = true
            WHERE user_id = :user_id AND read = false
        """),
        {"user_id": user_id},
    )
    await db.commit()
    return result.rowcount
