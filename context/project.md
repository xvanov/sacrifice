# Sacrifice

## Identity
Sacrifice is an accountability product where a user creates a goal, stakes money against failure, submits proof, and can have the pledge charged and donated if the goal is not verified before the deadline (`PRD.md`). The current repository delivers that product through a FastAPI backend, a Celery worker layer, a Click CLI, and a single Expo client that targets mobile and web (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `frontend/App.tsx`).

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy asyncio, Alembic, Celery, Redis, PostgreSQL via `asyncpg` (`backend/pyproject.toml`, `backend/app/config.py`)
- Client: Expo 54, React 19, React Native 0.81, TypeScript, NativeWind (`frontend/package.json`)
- Native app configuration: Expo managed `app.json` with datetime picker, secure store, and web browser plugins (`frontend/app.json`)
- CLI: Click console script exposed as `sacrifice` (`backend/pyproject.toml`, `backend/cli/main.py`)
- External integrations configured in settings: Google OAuth, GitHub OAuth, Stripe, YouTube, Docker-based sandboxing, and Azure Foundry (`backend/app/config.py`, `backend/pyproject.toml`)

## Top-level layout
- `backend/` — FastAPI app, goal-type registry, Celery workers, CLI, Alembic migrations, and tests
- `frontend/` — Expo application, native app config, screens, hooks, and shared API/auth services
- `context/` — canonical repository context files for later agents
- `PRD.md` — product intent and proof models the code is implementing
- `activity.md` — repository implementation log
- `Makefile`, `ralph.sh`, and `scripts/` — local development helpers and migration utilities

## Active constraints
- Local defaults assume PostgreSQL at `localhost:5433`, Redis at `localhost:6379`, and backend HTTP at `http://localhost:8000` (`backend/app/config.py`, `frontend/services/api.ts`).
- FastAPI CORS is an explicit allowlist of local Expo and web origins plus one ngrok origin; it is not open-ended (`backend/app/main.py`).
- Goal creation and persistence still hard-code four goal types: `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- Proof submission is still JSON-based: the frontend request helper always sends `application/json`, the video proof screen asks for a pasted YouTube URL, and the backend submit-proof route consumes `ProofSubmissionCreate` rather than file uploads (`frontend/services/api.ts`, `frontend/screens/ProofSubmissionScreen.tsx`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`).
- The Expo app has no camera or media-capture plugin configuration today; `app.json` only lists datetime picker, secure store, and web browser plugins (`frontend/package.json`, `frontend/app.json`).
- Frontend and mobile changes should follow the exact Expo 54 docs called out in `frontend/AGENTS.md` (`frontend/AGENTS.md`).
