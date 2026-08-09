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


async def notify_goal_resolution(db: AsyncSession, goal, status: str) -> None:
    """Emit the user-facing notification when a goal is resolved by the system.

    Verified/failed goals are now set only by the verification/deadline workers
    (users can no longer self-transition). Those workers previously set the
    status directly and emitted NO notification — so real verifications were
    silent; only the old (insecure) user-PUT path notified. This makes the
    resolution notification fire for the real pipeline path.
    """
    if status == "verified":
        await create_notification(
            db,
            user_id=goal.user_id,
            notification_type="goal_completed",
            title=f"Goal Completed: {goal.title}",
            body=f"Your goal '{goal.title}' has been verified successfully!",
            goal_id=goal.id,
        )
    elif status == "failed":
        await create_notification(
            db,
            user_id=goal.user_id,
            notification_type="goal_failed",
            title=f"Goal Failed: {goal.title}",
            body=(
                f"Your goal '{goal.title}' was not verified. Your pledge of "
                f"${goal.pledge_amount / 100:.2f} will be charged and donated "
                f"to your selected charity."
            ),
            goal_id=goal.id,
        )


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


async def get_total_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == user_id,
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
