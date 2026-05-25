# backend-workers

## What this module is
The worker layer is split between Celery configuration in `backend/app/core/` and task implementations in `backend/app/workers/`. It owns asynchronous verification, scheduled deadline enforcement, recurring goal rollover, and payment/disbursement work (`backend/app/core/celery_app.py`, `backend/app/workers/deadline.py`, `backend/app/workers/payments.py`).

## Entry points read
- `backend/app/core/celery_app.py`
- `backend/app/workers/deadline.py` (task names and recurrence behavior extracted via grep)
- `backend/app/workers/payments.py` (payment and transfer behavior extracted via grep)

## Public shape
The Celery app is named `sacrifice`, uses Redis for both broker and result backend, serializes payloads as JSON, runs in UTC, and includes these worker modules (`backend/app/core/celery_app.py`):
- `app.workers.youtube`
- `app.workers.api_check`
- `app.workers.dev_sandbox`
- `app.workers.github_repo`
- `app.workers.payments`
- `app.workers.deadline`

The beat schedule currently runs `app.workers.deadline.check_deadlines` every 60 seconds (`backend/app/core/celery_app.py`).

## Notable current behaviors
- Deadline processing computes the next deadline for `daily`, `weekly`, and `monthly` recurrence and creates next-period goal instances when recurrence is enabled (`backend/app/workers/deadline.py`).
- The goal routes enqueue worker tasks for YouTube, dev sandbox, and GitHub repo proof verification; the Celery include list shows API endpoint verification is also implemented as a worker concern (`backend/app/routes/goals.py`, `backend/app/core/celery_app.py`).
- Payment processing calculates a transfer amount after fees and uses Stripe transfer creation fields such as `transfer_group` and persisted transfer IDs (`backend/app/workers/payments.py`).
- The activity log says deadline enforcement and payment processing are already implemented, with notification side effects and retry handling called out there (`activity.md`).

## Integration edges
- Consumes jobs created by API routes.
- Uses Redis as queue and result storage.
- Reads and writes goal/payment state in the database.
- Calls external verification targets and Stripe.

## Change guidance
Use this module when the work is asynchronous, deadline-driven, or external-service heavy. If a request should return quickly and defer real work, the API should enqueue here rather than trying to complete the operation inline.
