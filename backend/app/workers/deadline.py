import asyncio
import logging
from datetime import datetime, timezone, timedelta

from celery import Task
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.celery_app import celery_app
from app.workers.payments import process_charge_for_goal

logger = logging.getLogger(__name__)

GRACE_PERIOD_MINUTES = 5


def _get_session():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


async def check_deadlines():
    now = datetime.now(timezone.utc)
    grace_threshold = now - timedelta(minutes=GRACE_PERIOD_MINUTES)

    engine, session_factory = _get_session()
    async with session_factory() as db:
        try:
            active_expired = await db.execute(
                text("""
                    SELECT id, user_id FROM goals
                    WHERE status = 'active' AND deadline < :now
                """),
                {"now": now},
            )
            active_rows = list(active_expired)

            for row in active_rows:
                goal_id_str = str(row[0])
                user_id_str = str(row[1])
                await db.execute(
                    text("UPDATE goals SET status = :status WHERE id = :id"),
                    {"status": "failed", "id": row[0]},
                )
                try:
                    await process_charge_for_goal(goal_id_str, user_id_str)
                except Exception as e:
                    logger.error("Failed to process charge for goal %s: %s", goal_id_str, e)

            pending_expired = await db.execute(
                text("""
                    SELECT g.id, g.user_id FROM goals g
                    WHERE g.status = 'pending_review' AND g.deadline < :grace_threshold
                """),
                {"grace_threshold": grace_threshold},
            )
            pending_rows = list(pending_expired)

            for row in pending_rows:
                goal_id_str = str(row[0])
                user_id_str = str(row[1])
                await db.execute(
                    text("UPDATE goals SET status = :status WHERE id = :id"),
                    {"status": "failed", "id": row[0]},
                )
                try:
                    await process_charge_for_goal(goal_id_str, user_id_str)
                except Exception as e:
                    logger.error("Failed to process charge for goal %s: %s", goal_id_str, e)

            await db.commit()

            return {
                "processed_active": len(active_rows),
                "processed_pending": len(pending_rows),
            }

        finally:
            await db.close()
            await engine.dispose()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def check_deadlines_task(self: Task):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(check_deadlines())
        return result
    finally:
        loop.close()
