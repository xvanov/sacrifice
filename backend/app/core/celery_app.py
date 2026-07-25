from celery import Celery

from app.config import settings
from app.goal_types.registry import get_celery_include_modules

celery_app = Celery(
    "sacrifice",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=get_celery_include_modules(),
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "check-deadlines": {
            # Must be the REGISTERED task name. ``check_deadlines`` is the bare
            # coroutine; the Celery task that wraps it is ``check_deadlines_task``
            # (registered as ``app.workers.deadline.check_deadlines_task``).
            # The old name dispatched to nothing — beat logged "unregistered
            # task" every 60s and no goal was ever auto-failed past its deadline.
            "task": "app.workers.deadline.check_deadlines_task",
            "schedule": 60.0,
        },
        "reconcile-verification-dispatch": {
            # Re-queues verification for proofs whose task never reached the
            # worker. Without it a broker hiccup leaves the proof "pending"
            # forever while the deadline sweep charges the pledge.
            "task": "app.workers.reconcile_dispatch.reconcile_dispatch_task",
            "schedule": 60.0,
        },
    },
)
