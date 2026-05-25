# Current State

## Active architectural decisions
Sacrifice currently runs as a split system with four working surfaces: an HTTP API in `backend/app`, asynchronous workers under `backend/app/core` and `backend/app/workers`, a Click-based CLI in `backend/cli`, and an Expo client in `frontend/`. The backend API mounts routers for health, auth, dashboard, goals, notifications, and payments (`backend/app/main.py`).

The API persists through SQLAlchemy async sessions created from `settings.database_url`, which defaults to a PostgreSQL DSN using `asyncpg` (`backend/app/config.py`, `backend/app/database.py`). Background work is delegated to Celery using Redis as both broker and result backend, with a beat schedule that runs deadline checks every 60 seconds in UTC (`backend/app/core/celery_app.py`).

Goal proof handling is asynchronous. The goal routes branch on goal type and enqueue verification work for YouTube, dev sandbox, and GitHub repo submissions; the worker registry also includes API endpoint verification and payment/deadline processing (`backend/app/routes/goals.py`, `backend/app/core/celery_app.py`). The currently visible proof-related goal types in code are `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/routes/goals.py`, `frontend/services/api.ts`, `backend/cli/main.py`).

The frontend is a single Expo app that uses `AuthProvider` and `NavigationProvider` from local hooks and selects screens by checking `currentScreen.name` in `App.tsx`; there is no external router visible in the files read. Its API layer adds a bearer token when present and clears local auth on HTTP 401 (`frontend/App.tsx`, `frontend/services/api.ts`).

Legacy docs still matter for interpretation, but the implementation log is ahead of the original MVP in at least one area: the PRD names YouTube, API endpoint, and dev sandbox verification, while current routes and clients also expose GitHub repo verification (`PRD.md`, `backend/app/routes/goals.py`, `frontend/services/api.ts`, `backend/cli/main.py`).

## Module map

| Module | Path | Responsibility now | Evidence read |
| --- | --- | --- | --- |
| frontend | `frontend/` | Authenticated mobile/web client for login, goal creation, goal detail, proof submission, dashboard, and notifications | `frontend/App.tsx`, `frontend/services/api.ts`, `frontend/package.json`, `frontend/AGENTS.md` |
| backend-app | `backend/app/` | FastAPI service, CORS, router composition, settings, database session management, goal-facing HTTP API | `backend/app/main.py`, `backend/app/config.py`, `backend/app/database.py`, `backend/app/routes/goals.py` |
| backend-workers | `backend/app/core/` and `backend/app/workers/` | Celery queue setup, scheduled deadline checks, async verification, payment/disbursement orchestration | `backend/app/core/celery_app.py`, `backend/app/workers/deadline.py`, `backend/app/workers/payments.py` |
| backend-cli | `backend/cli/` | Local command-line access to auth, goals, dashboard, and notifications APIs | `backend/cli/main.py`, `backend/cli/client.py`, `backend/pyproject.toml` |

## Current constraints
- Local development assumptions are hard-coded in defaults: backend `frontend_url`, database DSN, Redis URL, and frontend API base URL all point at localhost-first setups (`backend/app/config.py`, `frontend/services/api.ts`).
- OAuth, Stripe, YouTube, and Azure Foundry integrations are settings-driven and require environment values to be useful (`backend/app/config.py`).
- `debug` defaults to `True` and the JWT secret defaults to a placeholder string, which is acceptable for local work but should be treated as a deployment constraint, not a production-ready posture (`backend/app/config.py`).
- The activity log reports completed work for payments, notifications, dashboard, recurring goals, and multiple proof flows as of 2026-05-18, so agents should assume those areas already exist before proposing new scaffolding (`activity.md`).
