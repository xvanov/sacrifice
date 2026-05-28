# Sacrifice

## Identity
Sacrifice is an accountability app: a user creates a goal, stakes money against failure, submits proof before a deadline, and can have that pledge charged and donated if the goal is not verified (`PRD.md`). The repository already delivers that product as a FastAPI backend plus Celery workers and a single Expo client; the generator-pipeline direction has to extend that existing product rather than replace it (`backend/app/main.py`, `backend/app/core/celery_app.py`, `frontend/App.tsx`).

For the generator direction specifically, the live codebase already has one promising seam: backend goal verification is discovered from filesystem goal-type packages under `backend/app/goal_types/`. But the rest of the product is still mostly fixed-shape around four built-in goal types, so generated modules do not yet flow end-to-end through creation, proof submission, and mobile capture (`backend/app/goal_types/registry.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `frontend/screens/GoalCreateScreen.tsx`).

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy asyncio, Alembic, Celery, Redis, PostgreSQL via `asyncpg` (`backend/pyproject.toml`, `backend/app/config.py`)
- Frontend: Expo `~54.0.33`, React 19, React Native 0.81, TypeScript, NativeWind (`frontend/package.json`)
- Native surface: Expo managed app config with datetime picker, secure store, and web browser plugins only (`frontend/app.json`)
- Automation/config surface: `PROMPT.md` for repo work instructions, `activity.md` for recent progress, and `opencode.json` for the external agent runner configuration (`PROMPT.md`, `activity.md`, `opencode.json`)
- Integrations configured in settings: Google OAuth, GitHub OAuth, Stripe, YouTube, Docker-based sandboxing, and Azure Foundry (`backend/app/config.py`)

## Top-level layout
- `backend/` — FastAPI app, goal-type packages, proof routes, Celery config, workers, CLI, and tests
- `frontend/` — Expo app shell, handwritten navigation, goal creation screens, proof submission screens, and API/auth helpers
- `context/` — canonical context files for later agents
- `PRD.md` — product requirements and task inventory
- `PROMPT.md` — repository-specific execution rules for agents
- `activity.md` — implementation log of completed work
- `opencode.json` — model/provider/skills/permission config for the external factory-style runner

## Active constraints
- Repo guidance says to read `activity.md` before `PRD.md`, not to start backend or Expo manually because the orchestrator already binds ports `8000` and `8082`, and to treat Celery as opt-in unless a task genuinely needs it (`PROMPT.md`).
- Frontend and mobile work should follow the exact Expo docs version called out by the repo: `https://docs.expo.dev/versions/v54.0.0/` (`frontend/AGENTS.md`, `frontend/package.json`).
- Goal creation is still fixed to `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` in the client type union, backend schema validation, and database enums (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- Proof submission is still JSON-only. The frontend API helper always sends `Content-Type: application/json`, proof bodies are `JSON.stringify`-ed, and the backend accepts a flat `ProofSubmissionCreate` model rather than uploads (`frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`).
- There is no app-internal chat surface or generator runner in the files read. The current app remains screen-based and form-based, while factory/orchestration behavior is expressed through repo config files rather than shipped runtime code (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `opencode.json`, `PROMPT.md`).
- A phone-camera proof flow is still missing its shared infrastructure: no camera plugin in Expo config, no upload transport in the client, and no media-upload endpoint in the backend (`frontend/app.json`, `frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`).
