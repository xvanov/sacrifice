# Sacrifice

## Identity
Sacrifice is an accountability product where a user creates a goal, stakes money against failure, submits proof, and can have the pledge charged and donated if the goal is not verified before the deadline (`PRD.md`). The current repository implements that product as a FastAPI backend, Celery worker layer, Click CLI, and Expo/React Native client (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `frontend/App.tsx`).

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy asyncio, Alembic, Celery, Redis, PostgreSQL via `asyncpg` (`backend/pyproject.toml`, `backend/app/config.py`)
- Frontend: Expo 54, React 19, React Native 0.81, TypeScript, NativeWind (`frontend/package.json`)
- CLI: Click console script exposed as `sacrifice` (`backend/pyproject.toml`, `backend/cli/main.py`)
- External services configured in settings: Google OAuth, GitHub OAuth, Stripe, YouTube, Docker-based sandboxing, Azure Foundry (`backend/app/config.py`, `backend/pyproject.toml`)

## Top-level layout
- `backend/` — FastAPI app, Celery workers, CLI, Alembic migrations, and tests
- `frontend/` — Expo application with screens, hooks, components, and API/auth services
- `context/` — canonical repository context files for later agents
- `PRD.md` — product intent and MVP-level user flows
- `activity.md` — implementation log through 2026-05-18
- `Makefile`, `ralph.sh`, `logs/`, `screenshots/` — local development helpers and artifacts

## Active constraints
- Local defaults assume PostgreSQL at `localhost:5433`, Redis at `localhost:6379`, and backend HTTP at `http://localhost:8000` (`backend/app/config.py`, `frontend/services/api.ts`).
- FastAPI CORS is an explicit allowlist of local Expo/web origins plus one ngrok origin; it is not open-ended (`backend/app/main.py`).
- Goal creation is still a typed flow: the frontend asks the user to choose one of four hard-coded goal types before building a `POST /api/goals` payload (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/schemas/goal.py`).
- The currently accepted goal types are `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`, and they are enforced in both request validation and database enums (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- Frontend changes should follow the exact Expo 54 docs called out in `frontend/AGENTS.md` (`frontend/AGENTS.md`).
