# backend

## What this module is
`backend/` is the Python server-side surface for Sacrifice. It combines the FastAPI API in `backend/app/`, Celery queue configuration and worker modules under `backend/app/core/` and `backend/app/workers/`, and a Click CLI in `backend/cli/` that talks to the same HTTP API (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `backend/pyproject.toml`).

## Entry points and shape files read
- `backend/app/main.py`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`
- `backend/app/schemas/proof.py`
- `backend/app/models/goal.py`
- `backend/app/core/celery_app.py`
- `backend/cli/main.py`

## Public shape now
- `backend/app/main.py` mounts routers for health, auth, dashboard, goals, notifications, and payment.
- `backend/app/routes/goals.py` exposes goal create/list/detail/update/delete, proof submission, and verification-status endpoints.
- `backend/cli/main.py` is the local CLI entrypoint and formats goal records and verification status while calling the backend API.

## Current goal-type contract
The backend does not yet have a pluggable `GoalType` contract. Goal-type behavior is hard-coded in several layers:

- `GoalCreate.validate_goal_type()` accepts only `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/schemas/goal.py`).
- `Goal.goal_type` and `GoalCriteria.criteria_type` persist those choices as SQLAlchemy enums (`backend/app/models/goal.py`).
- `ProofSubmissionCreate` is a single wide request model, and `backend/app/schemas/proof.py` defines one proof schema class per supported type instead of a discoverable plugin interface.
- `submit_proof()` in `backend/app/routes/goals.py` branches on `goal.goal_type` with `if/elif` blocks and performs validation, proof-data construction, and task dispatch inline.
- `backend/app/core/celery_app.py` hard-codes one worker include per type: `app.workers.youtube`, `app.workers.api_check`, `app.workers.dev_sandbox`, and `app.workers.github_repo`.

## Registry endpoint status
There is no backend registry endpoint for goal types in the files read. The FastAPI app mounts the routers listed above, and the goal routes do not expose a `GET /api/goal-types` or similar discovery surface (`backend/app/main.py`, `backend/app/routes/goals.py`).

## Current extension barrier
Adding a new goal type today is a coordinated backend-plus-client change, not a single-directory drop-in. A new type currently requires updates in:

1. `backend/app/schemas/goal.py` for the allowed type set.
2. `backend/app/models/goal.py` for persisted enum values.
3. `backend/app/schemas/proof.py` for proof-shape validation.
4. `backend/app/routes/goals.py` for another `if/elif` submission branch.
5. `backend/app/workers/` for a new verifier module.
6. `backend/app/core/celery_app.py` for worker registration.
7. `backend/cli/main.py` if CLI creation or submission support is needed.
8. Frontend API and screen wiring such as `frontend/services/api.ts`, `frontend/App.tsx`, `frontend/screens/GoalCreateScreen.tsx`, and `frontend/screens/GoalDetailScreen.tsx`.

## Change guidance
When working on goal-type extensibility, start by confirming whether the code change introduces a real backend-owned contract and discovery endpoint or whether it still depends on route branches and static worker registration. Until the code changes, treat new goal types as cross-layer work rather than isolated plugins.