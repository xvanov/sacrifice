# Current state

## Active architectural decisions
Sacrifice runs as one FastAPI service that wires together health, auth, dashboard, goal-type listing, goal CRUD/proof submission, notifications, and payment routes in `backend/app/main.py`. The backend exposes one HTTP surface to both the Expo client and the Click CLI, rather than separate mobile and automation backends (`backend/app/main.py`, `backend/cli/client.py`).

Goal-type extensibility currently starts at proof verification, not at full lifecycle creation. `backend/app/goal_types/registry.py` auto-discovers subpackages and `backend/app/routes/goals.py` resolves `goal.goal_type` through the registry for proof verification. The creation path still validates against a fixed allowlist in `backend/app/schemas/goal.py`, persists fixed PostgreSQL enums in `backend/app/models/goal.py`, and the frontend still hardcodes the same four-type union in `frontend/screens/GoalCreateScreen.tsx`.

The backend already publishes generator-friendly goal-type metadata. `GET /api/goal-types` iterates the registry and returns each type’s `name`, `description`, `sample_prompts`, and `criteria_schema`, but the current Expo goal-creation screen does not read that endpoint and instead renders its own fixed `GOAL_TYPES`/`GOAL_TYPE_LABEL` maps (`backend/app/routes/goals.py`, `frontend/screens/GoalCreateScreen.tsx`).

Proof submission is a JSON payload flow end to end. The frontend fetch wrapper always sends JSON bodies with `Content-Type: application/json`, and proof helpers in `frontend/services/api.ts` all post JSON objects. On the backend, proof data is flattened from the Pydantic model, stored in the `proof_submissions.proof_data` JSONB column, and returned with a separate verification-status shape (`frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/models/proof.py`).

Background work is configured but optional in day-to-day development. `backend/app/core/celery_app.py` builds a Redis-backed Celery app, includes per-goal worker modules via `get_celery_include_modules()`, and schedules deadline checks every 60 seconds. `PROMPT.md` explicitly says the orchestrator already runs backend and frontend, while Celery should only be started when a task genuinely needs it.

The mobile app is an Expo-managed single-app shell that uses local providers for auth and screen state. `frontend/App.tsx` loads fonts, holds splash-screen behavior, wraps `AuthProvider` and `NavigationProvider`, and switches among login, dashboard, goal creation/detail, proof submission, and notification screens by inspecting `currentScreen` instead of relying on a larger navigation framework.

The CLI is a thin HTTP client over the same API. `backend/cli/main.py` offers OAuth login, goal creation and inspection commands, dashboard access, and notification commands, while `backend/cli/client.py` stores access tokens and cached user info in `~/.config/sacrifice/config.json`.

Cross-machine migration is scripted rather than container-image-based. `scripts/migration/README.md` and `scripts/migration/bootstrap.sh` define a bundle/bootstrap flow that preserves `.env` files, `factory.db`, and the Sacrifice PostgreSQL dump, recreates Python and Node dependencies, starts Dockerized Postgres and Redis, runs Alembic, and smoke-tests the factory.

## Module map

| Module | Paths | Role | Current notes |
| --- | --- | --- | --- |
| backend | `backend/app/`, `backend/cli/` | FastAPI API, persistence, goal verification dispatch, optional workers, and CLI access to the same API | Registry-based proof verification exists, but goal creation and enums still hardcode four goal types. |
| frontend | `frontend/` | Expo mobile/web client for auth, goal creation, proof submission, dashboard, and notifications | The app sends JSON-only requests and the current plugin set does not expose camera or upload primitives. |
| migration | `scripts/migration/` | Bundle/bootstrap automation for moving factory and Sacrifice state between machines | The scripts preserve database and env state, but recreate Redis and dependency installs on the destination machine. |

## Current constraints
- `GoalCreate` validation, `Goal`/`GoalCriteria` enums, and the goal-creation form still encode the built-in set `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `frontend/screens/GoalCreateScreen.tsx`).
- Proof payloads are still JSON documents stored in JSONB, and the frontend API wrapper has no multipart or binary upload path (`frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/models/proof.py`).
- Expo configuration currently enables only `@react-native-community/datetimepicker`, `expo-secure-store`, and `expo-web-browser`, so camera/file capture is not wired into the mobile shell (`frontend/app.json`).
- Backend settings assume a local web frontend and a PostgreSQL/Redis dev stack, with OAuth, Stripe, YouTube, and Azure Foundry credentials provided through environment variables (`backend/app/config.py`).
- Backend CORS currently permits specific localhost web origins plus one ngrok hostname, and the app keeps a legacy `/auth/github/callback` redirect shim for GitHub OAuth (`backend/app/main.py`).

<!-- factory:context-refresh ts=2026-07-18T04:05:13.854283+00:00 after_pr=#213 -->
