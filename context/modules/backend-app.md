# backend-app

## What this module is
`backend/app/` is the FastAPI service layer. It owns application settings, database session construction, router registration, chat-driven goal creation, and the main goal-facing HTTP surface (`backend/app/main.py`, `backend/app/config.py`, `backend/app/database.py`, `backend/app/routes/goals.py`, `backend/app/routes/chat.py`).

## Entry points read
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/database.py`
- `backend/app/routes/goals.py` (public interface extracted via decorators and goal-type branches)
- `backend/app/routes/chat.py` (chat session endpoints per D009 `api_spec.md`)

## Public shape
`main.py` creates `FastAPI(title="Sacrifice API", version="0.1.0")`, applies CORS middleware, and mounts routers for health, auth, dashboard, goals, chat, notifications, and payments. It also exposes a legacy GitHub callback redirect from `/auth/github/callback` to `/api/auth/github/callback` (`backend/app/main.py`).

The goal routes currently expose these HTTP surfaces (`backend/app/routes/goals.py`):
- `POST /api/goals`
- `GET /api/goals`
- `GET /api/goals/{goal_id}`
- `PUT /api/goals/{goal_id}`
- `DELETE /api/goals/{goal_id}`
- `POST /api/goals/{goal_id}/submit-proof`
- `GET /api/goals/{goal_id}/verification-status`

The chat routes expose these HTTP surfaces (`backend/app/routes/chat.py`):
- `POST /api/chat/sessions`
- `POST /api/chat/sessions/{session_id}/messages`
- `POST /api/chat/sessions/{session_id}/request-new-goal-type` (stub: returns 501; D010 replaces with real wiring)

## Notable current behaviors
- Proof submission branches on `goal.goal_type` and has type-specific validation for `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/routes/goals.py`).
- Goal creation and proof submission call the notification service; the activity log says status transitions also create notifications (`backend/app/routes/goals.py`, `activity.md`).
- Settings are environment-driven through `BaseSettings`, but development defaults are provided for PostgreSQL, Redis, frontend URL, OAuth callbacks, Stripe keys, Azure Foundry, and JWT configuration (`backend/app/config.py`).
- Database access is async-only in the files read: `create_async_engine(...)`, `async_sessionmaker(...)`, and `get_db()` yield an `AsyncSession` (`backend/app/database.py`).

## Integration edges
- Accepts authenticated requests from the Expo frontend and the CLI.
- Persists state in PostgreSQL, including `chat_sessions` with JSONB `messages` and `draft_goal` columns.
- Enqueues background verification work that is executed by Celery workers.
- Depends on OAuth providers and payment/LLM/video integrations configured through settings.

## Change guidance
When changing goal, payment, dashboard, notification, auth, or chat HTTP behavior, start here. Confirm whether the change belongs in router composition, request validation, notification side effects, or queue dispatch before touching workers or frontend code.
