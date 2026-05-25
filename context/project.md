# Sacrifice

## Identity
Sacrifice is an accountability product built around goals, deadlines, and financial pledges. The product requirement document describes the core promise clearly: a user creates a goal, stakes money against failure, submits proof, and the system verifies the proof before deciding whether the pledge should be charged and donated to a selected charity (`PRD.md`).

The current repository implements that idea as a multi-part app: a FastAPI backend, Celery workers, a Click CLI, and an Expo/React Native frontend (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `frontend/App.tsx`).

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy asyncio, Alembic, Celery, Redis, PostgreSQL via `asyncpg` (`backend/pyproject.toml`, `backend/app/config.py`, `backend/app/database.py`)
- Frontend: Expo 54, React Native 0.81, TypeScript, NativeWind (`frontend/package.json`)
- Integrations configured in code: Google OAuth, GitHub OAuth, Stripe, YouTube transcript/video APIs, Docker, Azure Foundry (`backend/pyproject.toml`, `backend/app/config.py`)
- CLI: `sacrifice` console script backed by Click (`backend/pyproject.toml`, `backend/cli/main.py`)

## Top-level layout
- `backend/` — API service, async database access, workers, CLI, Alembic migrations, tests
- `frontend/` — Expo application with screens, hooks, components, and API/auth services
- `PRD.md` — product intent and MVP scope
- `activity.md` — implementation log with feature-completion notes through 2026-05-18
- `Makefile`, `ralph.sh`, `logs/`, `screenshots/` — local development helpers and artifacts

## Active constraints
- Backend defaults to local infrastructure: PostgreSQL on `localhost:5433` and Redis on `localhost:6379` (`backend/app/config.py`, `backend/app/database.py`).
- Frontend defaults to `http://localhost:8000` unless `EXPO_PUBLIC_API_URL` is set (`frontend/services/api.ts`).
- CORS is an explicit allowlist of local Expo/web origins plus one ngrok origin; it is not open-ended (`backend/app/main.py`).
- Frontend-specific agent guidance says contributors should use the exact Expo 54 documentation set (`frontend/AGENTS.md`).
- Current-state context in this repo must be inferred from code plus the legacy `PRD.md` and `activity.md`; there was no canonical `context/` set before this run.
