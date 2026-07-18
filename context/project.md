# Sacrifice

## Identity
Sacrifice is an accountability app where a user creates a goal, stakes money against failure, submits proof before a deadline, and risks having that pledge charged and donated if the goal is not verified (`PRD.md`). The repository currently ships a FastAPI backend, an optional Celery/Redis worker path, a Click CLI inside `backend/`, and a single Expo client (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `frontend/App.tsx`).

The generator-oriented seam that already exists in live code is backend goal-type discovery. `backend/app/goal_types/registry.py` auto-discovers goal-type packages from the filesystem and `backend/app/routes/goals.py` dispatches proof verification through that registry, but goal creation, proof schemas, database enums, and mobile form unions are still fixed around four built-in goal types (`backend/app/goal_types/registry.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `frontend/screens/GoalCreateScreen.tsx`).

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy asyncio, Alembic, PostgreSQL via `asyncpg`, Celery, Redis, Click CLI (`backend/pyproject.toml`, `backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`)
- Frontend: Expo `~54.0.33`, React 19.1, React Native 0.81.5, TypeScript, NativeWind (`frontend/package.json`)
- Mobile surface: Expo managed app config with datetime picker, secure store, and web browser plugins only (`frontend/app.json`)
- Integrations configured in settings: Google OAuth, GitHub OAuth, YouTube, Stripe, Redis, PostgreSQL, and Azure Foundry (`backend/app/config.py`)
- Repo workflow/config surface: `PRD.md`, `activity.md`, `PROMPT.md`, and `opencode.json` (`PRD.md`, `activity.md`, `PROMPT.md`, `opencode.json`)

## Top-level layout
- `backend/` — FastAPI app composition, goal routes, goal-type registry, SQLAlchemy models, Celery config, backend tests, and the `sacrifice` CLI
- `frontend/` — Expo app shell, auth/navigation hooks, screen components, API helpers, and frontend tests
- `scripts/migration/` — cross-machine bundle/bootstrap scripts for preserving factory and Sacrifice state
- `context/` — canonical current-state docs for later agents
- `PRD.md` / `activity.md` / `PROMPT.md` / `opencode.json` — product requirements, implementation log, repo operating rules, and external runner config

## Active constraints
- Repo guidance says to read `activity.md` before `PRD.md`, not to start uvicorn or Expo manually because the orchestrator already binds ports `8000` and `8082`, and to treat Celery as opt-in unless a task genuinely needs it (`PROMPT.md`).
- Frontend work should follow the exact Expo docs version explicitly called out by the repo: `https://docs.expo.dev/versions/v54.0.0/` (`frontend/AGENTS.md`, `frontend/package.json`).
- The backend already exposes goal-type metadata at `/api/goal-types` (`name`, `description`, `sample_prompts`, and `criteria_schema`), but the current creation UI still builds its options from hardcoded local constants instead of consuming that endpoint (`backend/app/routes/goals.py`, `frontend/screens/GoalCreateScreen.tsx`).
- Goal creation is still fixed to `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` in the client union, backend schema validation, and database enums (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- Proof submission is still JSON-only. The frontend hardcodes `Content-Type: application/json` and `JSON.stringify`, while the backend accepts a flat `ProofSubmissionCreate` model and stores proof bodies in JSONB (`frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/models/proof.py`).
- There is no camera or upload path in the inspected mobile config: `frontend/app.json` only enables datetime picker, secure store, and web browser plugins.

<!-- factory:context-refresh ts=2026-07-18T04:05:27.208780+00:00 after_pr=#212 -->
