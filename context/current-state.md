# Current state

## Active architectural decisions
<<<<<<< HEAD
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
=======
Sacrifice still treats goal creation as a typed form flow in the frontend. `App.tsx` routes the `goal-create` screen name to `GoalCreateScreen`, and the navigation union has no chat, conversation, or matcher-specific screen today (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`). Inside `GoalCreateScreen`, the client keeps a local `GoalType` union of `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`, renders a different sub-form for each type, validates those type-specific inputs, and builds the final `criteria` object in the client before posting to `POST /api/goals` (`frontend/screens/GoalCreateScreen.tsx`).

The current creation contract is reinforced by tests. `GoalCreateScreen.test.tsx` asserts that the screen shows YouTube-specific fields by default, swaps in API and sandbox fields when the user taps the type pills, and submits JSON directly to `/api/goals` with the form-derived payload (`frontend/__tests__/screens/GoalCreateScreen.test.tsx`). Replacing typed goal creation therefore requires updating both the screen and the tests that currently encode its type-first behavior.

The creation screen also owns the payment-method warning and charity lookup. On mount it calls `getPaymentMethods()` and `searchCharities()`, then includes the selected `charity_id` in the same create-goal payload (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`). Any chat-first replacement still has to account for those non-goal-type parts of the flow.

The backend already has one reusable catalog of goal types, but it is not yet a prompt-matching API. `GET /api/goal-types` returns each registered plugin's `name`, `description`, `sample_prompts`, and `criteria_schema` for authenticated users, and the registry auto-discovers packages under `app.goal_types` (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`, `backend/tests/test_goal_types_api.py`). In the files read, though, `main.py` mounts no prompt-matching route, `routes/goals.py` exposes no create-from-prompt endpoint beside `POST /api/goals`, and the frontend API client does not call `/api/goal-types` today (`backend/app/main.py`, `backend/app/routes/goals.py`, `frontend/services/api.ts`).

Goal creation on the server is still explicit and type-first. `GoalCreate` requires both `goal_type` and `criteria`, validates `goal_type` against the same four core names, `create_goal()` persists the submitted type and criteria row directly, and the database enums still encode fixed values for `Goal.goal_type` and `GoalCriteria.criteria_type` (`backend/app/schemas/goal.py`, `backend/app/services/goal.py`, `backend/app/models/goal.py`). A chat-driven creation path therefore still has to resolve a concrete `goal_type` plus concrete `criteria` before it can reuse the existing create flow.

LLM usage exists today, but only for proof verification. `app.services.llm` exposes transcript and code-authenticity judges for YouTube and dev-sandbox proof review; there is no prompt-to-goal-type classifier in the files read (`backend/app/services/llm.py`). The plugin catalog exposes enough descriptive metadata for a matcher, but the matcher itself is not present yet.

Proof submission remains separate from creation and should stay separate in this batch. `App.tsx` still routes to the existing proof-submission screens, `api.ts` still posts proof payloads as JSON, and `submit_proof()` remains goal-type-agnostic by dispatching through the registry (`frontend/App.tsx`, `frontend/services/api.ts`, `backend/app/routes/goals.py`).

## Module map

| Module | Path | Responsibility now | Chat-creation relevance |
| --- | --- | --- | --- |
| frontend | `frontend/` | App shell, in-memory navigation, goal creation UI, proof submission entry points, and shared HTTP helpers (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`) | Owns removal of the goal-type picker and typed sub-forms, plus any prompt composer or match-review UX |
| backend | `backend/` | FastAPI routes, plugin registry, goal persistence, proof dispatch, and current LLM verification helpers (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/services/goal.py`, `backend/app/goal_types/registry.py`, `backend/app/services/llm.py`) | Owns any matcher endpoint or service that turns free text into a supported `goal_type` and `criteria` |

## Current constraints
- The current creation screen still assumes the user chooses a goal type up front; it renders four typed sub-forms and builds the `criteria` payload locally (`frontend/screens/GoalCreateScreen.tsx`).
- The shared frontend API client has `createGoal()`, `searchCharities()`, and payment helpers, but no helper for fetching the goal-type catalog or posting a prompt for matching (`frontend/services/api.ts`).
- The backend exposes `/api/goal-types`, but no prompt-matching or prompt-driven creation route is mounted beside `/api/goals` (`backend/app/main.py`, `backend/app/routes/goals.py`).
- Server-side validation and persistence still hard-code the four core types, so a matcher cannot return an arbitrary new registry name without additional schema and database work (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `backend/app/services/goal.py`).
- Existing frontend tests encode the typed UX and current request shape, so replacing the creation surface requires rewriting those expectations rather than only changing production code (`frontend/__tests__/screens/GoalCreateScreen.test.tsx`).
- No "no match → generate a new goal type" path exists in the files read; that follow-on factory behavior is not part of the current backend surface (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/services/llm.py`).
- Proof submission screens and proof verification routing already key off the stored `goal_type`; they are downstream of creation and should remain untouched in this batch (`frontend/App.tsx`, `backend/app/routes/goals.py`).
>>>>>>> origin/main
