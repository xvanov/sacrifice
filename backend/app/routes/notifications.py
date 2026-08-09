import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.notification import (
    get_total_count,
    get_unread_count,
    get_user_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _build_notification_response(notif) -> dict:
    return {
        "id": str(notif.id),
        "user_id": str(notif.user_id),
        "goal_id": str(notif.goal_id) if notif.goal_id else None,
        "type": notif.type,
        "title": notif.title,
        "body": notif.body,
        "read": notif.read,
        "created_at": notif.created_at.isoformat(),
    }


@router.get("")
async def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notifs = await get_user_notifications(db, current_user.id, limit, offset)
    return [_build_notification_response(n) for n in notifs]


@router.get("/unread-count")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = await get_unread_count(db, current_user.id)
    return {"unread_count": count}


@router.get("/count")
async def total_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = await get_total_count(db, current_user.id)
    return {"count": count}


@router.put("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        nid = uuid.UUID(notification_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    ok = await mark_notification_read(db, current_user.id, nid)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return {"status": "ok"}


@router.put("/read-all")
async def read_all(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await mark_all_notifications_read(db, current_user.id)
    return {"status": "ok"}
