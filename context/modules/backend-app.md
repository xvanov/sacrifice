# backend-app

## What this module is
`backend/app/` is the FastAPI service layer. It owns application settings, database session construction, router registration, and the main goal-facing HTTP surface (`backend/app/main.py`, `backend/app/config.py`, `backend/app/database.py`, `backend/app/routes/goals.py`).

## Entry points read
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/database.py`
- `backend/app/routes/goals.py` (public interface extracted via decorators and goal-type branches)

## Public shape
`main.py` creates `FastAPI(title="Sacrifice API", version="0.1.0")`, applies CORS middleware, and mounts routers for health, auth, dashboard, goals, notifications, and payments. It also exposes a legacy GitHub callback redirect from `/auth/github/callback` to `/api/auth/github/callback` (`backend/app/main.py`).

The goal routes currently expose these HTTP surfaces (`backend/app/routes/goals.py`):
- `POST /api/goals`
- `GET /api/goals`
- `GET /api/goals/{goal_id}`
- `PUT /api/goals/{goal_id}`
- `DELETE /api/goals/{goal_id}`
- `POST /api/goals/{goal_id}/submit-proof`
- `GET /api/goals/{goal_id}/verification-status`

## Notable current behaviors
- Proof submission branches on `goal.goal_type` and has type-specific validation for `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/routes/goals.py`).
- Goal creation and proof submission call the notification service; the activity log says status transitions also create notifications (`backend/app/routes/goals.py`, `activity.md`).
- Settings are environment-driven through `BaseSettings`, but development defaults are provided for PostgreSQL, Redis, frontend URL, OAuth callbacks, Stripe keys, Azure Foundry, and JWT configuration (`backend/app/config.py`).
- Database access is async-only in the files read: `create_async_engine(...)`, `async_sessionmaker(...)`, and `get_db()` yield an `AsyncSession` (`backend/app/database.py`).

## Integration edges
- Accepts authenticated requests from the Expo frontend and the CLI.
- Persists state in PostgreSQL.
- Enqueues background verification work that is executed by Celery workers.
- Depends on OAuth providers and payment/LLM/video integrations configured through settings.

## Change guidance
When changing goal, payment, dashboard, notification, or auth HTTP behavior, start here first. Confirm whether the change belongs in router composition, request validation, notification side effects, or queue dispatch before touching workers or frontend code.
