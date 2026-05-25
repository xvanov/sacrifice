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
            "task": "app.workers.deadline.check_deadlines",
            "schedule": 60.0,
        },
    },
)
