# Backend module

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

## Active constraints
- The proof-verification seam is registry-driven, but the creation path is not fully dynamic yet; schema validation and database enums still hardcode four goal types (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- Goal routes flatten proof payloads into JSON and only call goal-type hooks that match the existing `verify` and optional `dispatch_verification` contract (`backend/app/routes/goals.py`).
- The CLI stores access tokens locally in `~/.config/sacrifice/config.json`, so it assumes a user-level machine context rather than project-local auth state (`backend/cli/client.py`).
- Celery is available for deadline and per-goal background work, but repo guidance says it is not running by default during normal development (`backend/app/core/celery_app.py`, `PROMPT.md`).
