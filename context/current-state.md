# Current State

## Active architectural decisions
Sacrifice currently runs as a small monorepo with three active surfaces: an Expo client, a FastAPI backend, and a Click-based CLI. The backend assembles one FastAPI app and mounts routers for health, auth, dashboard, goals, notifications, and payments in `backend/app/main.py`.

Goal verification is asynchronous. `backend/app/routes/goals.py` stores proof submissions and dispatches Celery tasks for `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` work. `backend/app/core/celery_app.py` includes worker modules for those verification paths plus payments and deadline enforcement.

Deadline enforcement is also asynchronous. Celery beat schedules a deadline check every 60 seconds, and `backend/app/workers/deadline.py` can fail expired goals, create the next recurring instance, emit notifications, and trigger charging. Stripe charging and charity transfers are performed in `backend/app/workers/payments.py`.

The frontend keeps app state lightweight. `frontend/App.tsx` composes `AuthProvider` and `NavigationProvider`, `frontend/hooks/useAuth.tsx` manages session restoration and OAuth callback handling, and `frontend/hooks/useNavigation.tsx` uses React context instead of a larger routing library. The CLI talks to the same HTTP API and stores tokens in `~/.config/sacrifice/config.json` via `backend/cli/client.py`.

## Module map

| Module | Location | Entry point | Owns |
| --- | --- | --- | --- |
| Backend | `backend/app/` | `backend/app/main.py` | API assembly, auth, goals, dashboard, notifications, payments, async workers |
| Frontend | `frontend/` | `frontend/App.tsx` | Expo UI shell, auth state, screen switching, API client, typed goal data |
| CLI | `backend/cli/` | `backend/cli/main.py` | Browser-assisted login, goal commands, dashboard commands, notification commands |

## Current constraints
- Default runtime wiring assumes local PostgreSQL and Redis (`backend/app/config.py`).
- CORS origins are hard-coded in the FastAPI app for local ports plus one ngrok host (`backend/app/main.py`).
- `settings.debug` defaults to `True`, so the backend exposes `/api/auth/dev/token` unless settings are changed (`backend/app/config.py`, `backend/app/routes/auth.py`).
- Current goal types in code are `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`; current statuses include `draft`, `active`, `pending_review`, `verified`, `failed`, `payment_failed`, and `cancelled` (`frontend/types/index.ts`, `backend/app/routes/goals.py`).
- `activity.md` says the most recent recorded task is the in-app notification feed and records completed work through dashboard, payment, and notification features.
