# sacrifice-backend

## What this module is
The backend is the current system of record for goal creation, proof submission, notification side effects, and worker dispatch. The FastAPI app composes the active routers, the goals route owns the primary goal/proof HTTP surface, the schemas define the accepted payloads, and the Celery app wires background workers through the goal-type registry (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/schemas/proof.py`, `backend/app/core/celery_app.py`).

## Public shape now
- `backend/app/main.py` mounts health, auth, dashboard, goal-types, goals, notifications, and payment routers.
- `GET /api/goal-types` lists the discovered plugin metadata by reading `name`, `description`, `sample_prompts`, and `criteria_schema` from the registry.
- `POST /api/goals` creates a goal and emits a `goal_created` notification.
- `POST /api/goals/{goal_id}/submit-proof` accepts a `ProofSubmissionCreate` body, resolves the goal type from the registry, stores a pending `ProofSubmission`, and calls `dispatch_verification()` when available.
- `GET /api/goals/{goal_id}/verification-status` returns the latest stored verification result (`backend/app/routes/goals.py`).

## Current constraints
- `GoalCreate` still validates only four goal types: `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/schemas/goal.py`).
- `ProofSubmissionCreate` is a flat JSON model containing URL/request/repo/token-style fields, not a file-upload contract (`backend/app/schemas/proof.py`).
- The registry drives listing and proof dispatch, but create-time validation is still hard-coded outside the registry (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`, `backend/app/schemas/goal.py`).
- Celery worker inclusion is derived from registered goal types plus the shared payments and deadline workers (`backend/app/core/celery_app.py`, `backend/app/goal_types/registry.py`).

## Why it matters for D010
Any chat-factory generation flow eventually has to land on this backend surface. Today that means either producing a normal `GoalCreate` payload up front or changing the current backend contract; it also means generated goal types must integrate with registry discovery and the existing proof-dispatch path rather than bypassing them (`backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/goal_types/registry.py`).

## Files read
- `backend/app/main.py`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`
- `backend/app/schemas/proof.py`
- `backend/app/core/celery_app.py`
- `backend/app/goal_types/registry.py`
