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
        "process-deferred-charges": {
            # Collects the pledge for goals whose midnight buffer
            # (goals.charge_after, set by the failure-resolution paths) has
            # elapsed. See app/services/charge_scheduling.py.
            #
            # 5 minutes, not 60 seconds: this only ever fires once per goal
            # (charge_after is cleared after the attempt) and money moving at
            # a slight delay past midnight is not time-critical the way
            # deadline enforcement is.
            "task": "app.workers.payments.process_deferred_charges_task",
            "schedule": 300.0,
        },
        "reconcile-verification-dispatch": {
            # Re-queues verification for proofs whose task never reached the
            # worker. Without it a broker hiccup leaves the proof "pending"
            # forever while the deadline sweep charges the pledge.
            "task": "app.workers.reconcile_dispatch.reconcile_dispatch_task",
            "schedule": 60.0,
        },
        "alert-blocked-goals": {
            # The only thing that reads the deadline sweep's skip list. A goal
            # blocked on an inconclusive verification is skipped by every sweep
            # forever, so without this the pledge is silently forgiven and the
            # "our team is looking into it" notification has nobody behind it.
            # See app/workers/blocked_goal_alert.py for what the alert does and
            # does not reach.
            #
            # 15 minutes, not 60 seconds: nothing here is time-critical (these
            # goals have already exhausted a retry budget measured in staleness
            # windows) and a per-minute ERROR line for the same goal would train
            # its reader to ignore the log.
            "task": "app.workers.blocked_goal_alert.alert_on_blocked_goals_task",
            "schedule": 900.0,
        },
    },
)
