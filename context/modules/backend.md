# Backend

## Purpose
The backend is the authoritative service layer. It exposes the HTTP API, persists goals and related records, handles authentication, and dispatches asynchronous verification, deadline, and payment work.

## Entry point
- `backend/app/main.py` creates the FastAPI app, configures CORS, and includes the health, auth, dashboard, goals, notifications, and payment routers.

## Shape
- `backend/app/routes/` contains request handlers.
- `backend/app/models/` contains SQLAlchemy models for users, goals, proofs, payments, and notifications.
- `backend/app/schemas/` contains Pydantic request/response models.
- `backend/app/services/` contains business logic helpers.
- `backend/app/workers/` contains Celery task implementations for verification, deadlines, and payments.
- `backend/app/core/` contains cross-cutting runtime pieces like Celery setup, crypto, and auth dependencies.

## Important behavior
- Goal proof submission is type-aware in `backend/app/routes/goals.py` and enqueues different workers depending on `goal.goal_type`.
- Deadline checks run every 60 seconds from Celery beat, not inline in the API request path (`backend/app/core/celery_app.py`, `backend/app/workers/deadline.py`).
- Payment charging and charity transfer logic live in `backend/app/workers/payments.py`.
- Browser OAuth flows protect state with cookies; CLI/mobile flows use encoded state and redirect routing in `backend/app/routes/auth.py`.

## Read next
- For auth: `backend/app/routes/auth.py`
- For goals: `backend/app/routes/goals.py`, `backend/app/services/goal.py`
- For async behavior: `backend/app/core/celery_app.py`, `backend/app/workers/deadline.py`, `backend/app/workers/payments.py`
