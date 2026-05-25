# Project

## Identity
Sacrifice is a monorepo for an accountability platform. The product idea in `PRD.md` is simple: users define goals, put money at risk, submit proof, and donate that money to a chosen charity if they fail.

## Stack
- **Frontend:** Expo 54, React 19, React Native 0.81, TypeScript, NativeWind (`frontend/package.json`)
- **Backend:** Python 3.11, FastAPI, SQLAlchemy asyncio, asyncpg, Alembic, Celery, Redis, Stripe (`backend/pyproject.toml`, `backend/app/main.py`, `backend/app/core/celery_app.py`)
- **CLI:** Click + httpx, packaged as `sacrifice` (`backend/pyproject.toml`, `backend/cli/main.py`, `backend/cli/client.py`)

## Top-level layout
- `backend/app/` — FastAPI app, routes, models, services, worker code
- `backend/cli/` — packaged CLI for logging in and operating against the same backend API
- `backend/tests/` — pytest suite
- `frontend/screens/`, `frontend/components/`, `frontend/hooks/`, `frontend/services/`, `frontend/types/` — Expo client structure
- `Makefile` — local stack orchestration for Postgres, backend, frontend, Celery, logs, and tests
- `activity.md` — implementation log describing what has already been built
- `PRD.md` — original long-form product requirements

## Active constraints
- No repo-root `README.md` or prior canonical `context/` set existed during this onboarding pass.
- Backend settings default to PostgreSQL on `localhost:5433` and Redis on `localhost:6379` (`backend/app/config.py`).
- Frontend API calls default to `EXPO_PUBLIC_API_URL` or `http://localhost:8000` (`frontend/services/api.ts`).
- The frontend agent note explicitly says to use Expo v54 documentation (`frontend/AGENTS.md`).
- The local dev workflow assumes backend on `:8000`, frontend web on `:8082`, and a Docker container named `sacrifice-db` (`Makefile`).
