# Sacrifice

## Identity
Sacrifice is an accountability app built around financial stakes. The product requirement document describes a flow where a user creates a goal, adds a pledge amount, selects a charity, submits proof before a deadline, and is charged only if the proof fails or never arrives (`PRD.md`). The current repository implements that idea as a multi-part codebase with a FastAPI backend, an Expo frontend, and a small Python CLI (`backend/pyproject.toml`, `backend/app/main.py`, `frontend/App.tsx`, `backend/cli/main.py`).

## Stack
- **Backend:** Python 3.11, FastAPI, SQLAlchemy async ORM, Alembic, Celery, Redis, Stripe, YouTube transcript access, and Azure Foundry settings (`backend/pyproject.toml`, `backend/app/config.py`, `backend/app/database.py`).
- **Frontend:** Expo SDK 54, React 19, React Native / React Native Web, TypeScript, NativeWind, Jest (`frontend/package.json`, `frontend/App.tsx`, `frontend/AGENTS.md`).
- **CLI:** Click + httpx, exposed as the `sacrifice` script (`backend/pyproject.toml`, `backend/cli/main.py`, `backend/cli/client.py`).

## Top-level layout
- `backend/` — API service, data models, route handlers, workers, Alembic, tests, and CLI package.
- `frontend/` — Expo application with screen components, hooks, shared UI, and REST client.
- `PRD.md` — product scope and target flows for goals, verification, payments, and notifications.
- `activity.md` — running implementation log for recent feature work.
- `Makefile` — local dev orchestration for Postgres, FastAPI, Expo web, and Celery.
- `logs/` and `screenshots/` — runtime artifacts and captured images.

## Active constraints
- Backend settings load from `../.env` and default to a local PostgreSQL instance on port `5433` plus local Redis (`backend/app/config.py`).
- The FastAPI app hardcodes a development-oriented CORS allowlist for localhost Expo/web origins and one ngrok URL (`backend/app/main.py`).
- The Expo app uses a custom `AuthProvider` plus an in-memory `NavigationProvider` instead of Expo Router or React Navigation (`frontend/App.tsx`, `frontend/hooks/useAuth.tsx`, `frontend/hooks/useNavigation.tsx`).
- The local workflow assumes Docker can start a `sacrifice-db` container and that the backend runs on `8000` while the web frontend runs on `8082` (`Makefile`).
- The PRD names three MVP proof types (YouTube, API endpoint, Dev Sandbox), while the current backend also accepts a separate `github_repo` goal type (`PRD.md`, `backend/app/models/goal.py`, `backend/app/schemas/goal.py`).
