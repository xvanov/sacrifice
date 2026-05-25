# Current state

## Active architectural decisions
Sacrifice currently runs as a split repository with three active surfaces: an Expo client in `frontend/`, a FastAPI API in `backend/app`, and a Click-based CLI in `backend/cli`. The frontend and CLI both talk to the same backend REST API rather than owning separate backend logic (`frontend/App.tsx`, `frontend/services/api.ts`, `backend/cli/client.py`).

The backend is a single FastAPI application that mounts route groups for health, auth, dashboard, goals, notifications, and payments. Goal creation and proof submission are centralized in `app/routes/goals.py`; the proof endpoint branches on `goal_type` and enqueues background work for YouTube, API endpoint, dev sandbox, or GitHub repo verification (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/models/goal.py`).

Persistence currently centers on PostgreSQL through SQLAlchemy's async engine. Background verification is designed around Celery and Redis, while payment and charity discovery go through Stripe. The configuration surface also includes Google and GitHub OAuth, YouTube API access, and Azure Foundry settings for LLM-backed review (`backend/app/config.py`, `backend/app/database.py`, `backend/app/routes/payment.py`).

The frontend is intentionally simple at runtime. `App.tsx` selects a screen by checking auth state and the current navigation object, and the API client directly wraps `fetch` with bearer-token injection and token clearing on `401` responses (`frontend/App.tsx`, `frontend/hooks/useAuth.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts`).

## Module map

| Module | Directory | Entry point | Purpose |
| --- | --- | --- | --- |
| `backend` | `backend/app` | `backend/app/main.py` | FastAPI service for auth, goals, dashboard, notifications, and payments. |
| `cli` | `backend/cli` | `backend/cli/main.py` | Local command-line client for auth, goals, proof submission, and dashboard access. |
| `frontend` | `frontend` | `frontend/App.tsx` | Expo app for login, goal management, notifications, and proof submission flows. |

## Current constraints
- `backend/app/config.py` defaults `database_url` to `postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice` and `redis_url` to `redis://localhost:6379/0`, so local development assumes both services exist.
- Payment and charity search endpoints return configuration errors when `stripe_secret_key` is not set (`backend/app/routes/payment.py`).
- OAuth redirects depend on `frontend_url` and provider credentials in backend settings, and web auth depends on redirect handling in `useAuth.tsx` (`backend/app/config.py`, `backend/app/routes/auth.py`, `frontend/hooks/useAuth.tsx`).
- The frontend defaults to `http://localhost:8000` unless `EXPO_PUBLIC_API_URL` is set (`frontend/services/api.ts`).
- The CLI persists its token and user data in `~/.config/sacrifice/config.json`, so local machine state affects CLI behavior (`backend/cli/client.py`).
- Current code supports four goal types — `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` — even though the PRD's MVP narrative emphasizes the first three (`PRD.md`, `backend/app/models/goal.py`, `backend/app/schemas/goal.py`).
