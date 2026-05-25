from celery import Celery

from app.config import settings

celery_app = Celery(
    "sacrifice",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.youtube",
        "app.workers.api_check",
        "app.workers.dev_sandbox",
        "app.workers.github_repo",
        "app.workers.payments",
        "app.workers.deadline",
    ],
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
