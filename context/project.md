# Sacrifice

## Identity
Sacrifice is an accountability product where a user creates a goal, pledges money against failure, submits proof, and can have the pledge charged and donated if the goal is not verified before the deadline (`PRD.md`). In the codebase as scanned for D010, that product is implemented as a FastAPI backend, a Celery worker layer, a Click CLI, and a single Expo client; there is not yet a dedicated chat-factory creation surface in the frontend or backend entry points that were read (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts`).

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy asyncio, Alembic, Celery, Redis, PostgreSQL via `asyncpg` (`backend/pyproject.toml`, `backend/app/config.py`)
- Client: Expo 54, React 19, React Native 0.81, TypeScript, NativeWind (`frontend/package.json`)
- Native app configuration: Expo managed `app.json` with datetime picker, secure store, and web browser plugins only (`frontend/app.json`)
- CLI: Click console script exposed as `sacrifice`, aimed at the same backend API as the frontend (`backend/pyproject.toml`, `backend/cli/main.py`)
- Verification extension surface: goal-type plugins auto-discovered from `backend/app/goal_types/` and exposed through `/api/goal-types` plus proof dispatch (`backend/app/goal_types/base.py`, `backend/app/goal_types/registry.py`, `backend/app/routes/goals.py`)

## Top-level layout
- `backend/` — FastAPI routes, schemas, models, goal-type plugins, Celery configuration, workers, CLI, and tests
- `frontend/` — Expo application shell, local navigation, typed goal creation, proof submission screens, and shared API/auth helpers
- `context/` — canonical repo scan outputs, including the architecture docs used by later agents
- `PRD.md` — product-level requirements and current proof models
- `PROMPT.md` — repository task-runner instructions for implementation sessions
- `activity.md` — implementation log of completed tasks and verification runs
- `scripts/` — migration and local-environment helpers

## Active constraints
- The current frontend route map has `home`, `dashboard`, `goal-create`, `goal-detail`, proof submission screens, `notifications`, and `login`; there is no `chat` screen in `App.tsx` or `useNavigation.tsx` today (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).
- The shared frontend API client has goal, proof, dashboard, notification, payment, and charity helpers, but no chat transcript, matcher, or generator method; every request is JSON by default (`frontend/services/api.ts`).
- FastAPI mounts health, auth, dashboard, goal-types, goals, notifications, and payment routers only; there is no dedicated chat router in the current backend composition (`backend/app/main.py`, `backend/app/routes/goals.py`).
- Goal creation still hard-codes four allowed goal types — `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` — even though goal-type listing and proof dispatch already use the plugin registry (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/schemas/goal.py`, `backend/app/goal_types/registry.py`, `backend/app/routes/goals.py`).
- Proof submission is still JSON-based and artifact-specific: the YouTube proof screen asks for a pasted URL, `ProofSubmissionCreate` models URL/request/repo fields, and no upload or media-id contract appears in the scanned backend/frontend files (`frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`, `backend/app/schemas/proof.py`, `backend/app/routes/goals.py`).
- The Expo app has no camera or media-capture dependency/plugin configuration today; `package.json` and `app.json` expose datetime picker, secure store, and web browser, but no camera package or plugin (`frontend/package.json`, `frontend/app.json`).
- Frontend and mobile implementation work should stay aligned with the exact Expo 54 docs called out in repository guidance (`frontend/AGENTS.md`).
