# Backend module

<<<<<<< HEAD
## Purpose
`backend/` contains the FastAPI service, database models, goal-type registry, Celery configuration, tests, and the `sacrifice` Click CLI (`backend/app/main.py`, `backend/pyproject.toml`, `backend/cli/main.py`).

## Entry points and public surfaces
- `backend/app/main.py` creates the FastAPI app, installs CORS, mounts health/auth/dashboard/goal/goals-types/notifications/payment routers, and keeps a legacy GitHub OAuth callback redirect.
- `backend/app/routes/goals.py` is the main lifecycle surface for goal creation, listing, updating, deletion, proof submission, verification polling, and goal-type listing.
- `backend/app/core/celery_app.py` defines an optional Redis-backed Celery app and beat schedule for deadline checks.
- `backend/cli/main.py` exposes the same backend capabilities from the command line, and `backend/cli/client.py` is the shared HTTP client for that surface.

## Data and integration shape
- Settings in `backend/app/config.py` expect PostgreSQL, Redis, Google OAuth, GitHub OAuth, YouTube, Stripe, and Azure Foundry configuration from environment variables.
- `backend/app/models/goal.py` persists goals as PostgreSQL enums plus a separate `goal_criteria` row with JSONB criteria data.
- `backend/app/models/proof.py` stores proof payloads and verification details in JSONB, with verification state tracked independently from goal state.
- `backend/app/goal_types/registry.py` auto-discovers subpackages under `app.goal_types`, resolves live goal-type instances, and derives Celery include modules from the discovered set.
- The currently discoverable goal-type packages on disk are `api_endpoint`, `dev_sandbox`, `github_repo`, and `youtube_video`; the registry turns that filesystem shape into both the `/api/goal-types` metadata response and Celery include-module names (`backend/app/goal_types/registry.py`, `backend/app/routes/goals.py`, `backend/app/goal_types/`).

## Active constraints
- The proof-verification seam is registry-driven, but the creation path is not fully dynamic yet; schema validation and database enums still hardcode four goal types (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- Goal routes flatten proof payloads into JSON and only call goal-type hooks that match the existing `verify` and optional `dispatch_verification` contract (`backend/app/routes/goals.py`).
- The CLI stores access tokens locally in `~/.config/sacrifice/config.json`, so it assumes a user-level machine context rather than project-local auth state (`backend/cli/client.py`).
- Celery is available for deadline and per-goal background work, but repo guidance says it is not running by default during normal development (`backend/app/core/celery_app.py`, `PROMPT.md`).
=======
## What this module is
`backend/` is Sacrifice's server-side surface. `backend/app/main.py` mounts the FastAPI routers, `backend/app/routes/goals.py` owns the goal and goal-type HTTP endpoints, `backend/app/goal_types/registry.py` is the plugin discovery layer, `backend/app/services/goal.py` persists goals and criteria rows, and `backend/app/services/llm.py` contains the current LLM-backed verification helpers (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`, `backend/app/services/goal.py`, `backend/app/services/llm.py`).

## Entry points and shape files read
- `backend/app/main.py`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`
- `backend/app/models/goal.py`
- `backend/app/services/goal.py`
- `backend/app/goal_types/registry.py`
- `backend/app/services/llm.py`
- `backend/tests/test_goal_types_api.py`

## Public shape now
`backend/app/main.py` mounts the health, auth, dashboard, goal-types, goals, notifications, and payment routers. For goal creation, the important public routes are `GET /api/goal-types` and `POST /api/goals` (`backend/app/main.py`, `backend/app/routes/goals.py`).

`GET /api/goal-types` is the current backend-owned catalog surface. It returns every registered plugin's `name`, `description`, `sample_prompts`, and `criteria_schema`, and the tests assert both that response shape and the presence of the four core types (`backend/app/routes/goals.py`, `backend/tests/test_goal_types_api.py`). The registry behind that route auto-discovers subpackages under `app.goal_types` and exposes `list_types()` plus `get_type()` as the canonical lookup API (`backend/app/goal_types/registry.py`).

`POST /api/goals` is still explicit and non-conversational. It accepts `GoalCreate`, which requires `title`, `deadline`, `pledge_amount`, `goal_type`, and `criteria`, and the route passes that body directly into `create_goal()` (`backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/services/goal.py`). `create_goal()` persists a `Goal` row plus one `GoalCriteria` row, using `TYPE_TO_CRITERIA_TYPE` to map `youtube_video` to the stored `youtube` criteria type (`backend/app/services/goal.py`, `backend/app/models/goal.py`).

There is no prompt-matching or create-from-prompt backend surface in the files read. The mounted routers do not expose a prompt endpoint, `routes/goals.py` does not define a matcher function beside `create_goal_endpoint()`, and `services/llm.py` only contains proof-verification helpers for transcript review and code-authenticity review (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/services/llm.py`).

## Current chat-creation implications
- The plugin registry already provides the raw catalog a matcher needs: names, descriptions, sample prompts, and criteria schemas come from one backend source of truth (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`).
- The existing create-goal flow can be reused if a new matcher endpoint or service resolves the user's prompt into one of the currently supported `goal_type` values plus concrete `criteria` (`backend/app/schemas/goal.py`, `backend/app/services/goal.py`).
- Server-side hard-coding still limits the dynamic story. `GoalCreate` validates only the four core types, and the database enums for `Goal.goal_type` and `GoalCriteria.criteria_type` are fixed today (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- The current LLM helper layer is proof-specific, not creation-specific. Any new matching behavior needs either a new service in `app.services` or additional functions in `services/llm.py`; it cannot reuse an existing prompt classifier because none is present (`backend/app/services/llm.py`).
- The follow-on "no match → factory generates a new goal type" path is not part of the current backend surface; there is no route or service for it in the files read (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/services/llm.py`).

## Integration edges
- Exposes the authenticated goal-type catalog the frontend can use to drive chat matching (`backend/app/routes/goals.py`, `backend/tests/test_goal_types_api.py`).
- Exposes the create-goal contract that the frontend already uses and that any prompt-matching layer still has to satisfy, either directly or via a new translating endpoint (`backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/services/goal.py`).
- Owns proof dispatch through the same plugin registry, so creation changes should not require changes to proof submission in this batch (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`).

## Change guidance
For chat-driven goal creation, treat the registry catalog as the single source of truth for matchable goal types and keep the matcher close to the backend route layer. Do not add a second hard-coded list of goal types in a new matcher. If the matcher returns one of the existing four types, normalize its output back into the current `GoalCreate` shape so `create_goal()` and the existing persistence code can stay unchanged. Leave proof submission alone while creation changes land; the current proof route already dispatches by stored `goal_type` and is downstream of this work (`backend/app/routes/goals.py`, `backend/app/services/goal.py`, `backend/app/goal_types/registry.py`).
>>>>>>> origin/main
