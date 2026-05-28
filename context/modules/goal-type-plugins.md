# goal-type-plugins

## What this module is
The goal-type plugin system is the backend extension seam that turns a goal type name into verification behavior. `GoalTypeBase` defines the contract, `app.goal_types.registry` auto-discovers subpackages under `backend/app/goal_types/`, and concrete plugins like `youtube_video` provide metadata plus verification behavior (`backend/app/goal_types/base.py`, `backend/app/goal_types/registry.py`, `backend/app/goal_types/youtube_video/__init__.py`).

## Contract now
- Every plugin must subclass `GoalTypeBase` and implement `verify(proof_data, criteria_data)`.
- Plugins may also override `submit_proof(...)` and `dispatch_verification(...)`.
- The base contract exposes `name`, `description`, `sample_prompts`, and `criteria_schema` as the metadata fields surfaced through the API (`backend/app/goal_types/base.py`).

## Discovery and runtime use
- The registry walks subpackages in `backend/app/goal_types/`, imports them, and registers a module-level `goal_type` object when it is an instance of `GoalTypeBase` (`backend/app/goal_types/registry.py`).
- `GET /api/goal-types` exposes the discovered plugin metadata to authenticated clients (`backend/app/routes/goals.py`).
- `submit_proof()` resolves `goal.goal_type` through the registry before storing a pending submission and dispatching async verification (`backend/app/routes/goals.py`).
- Celery asks the registry for worker include modules so each registered type can supply its own worker path indirectly (`backend/app/core/celery_app.py`, `backend/app/goal_types/registry.py`).

## Concrete example read
The `youtube_video` plugin loads metadata from `definition`, validates a YouTube proof body, normalizes it into `video_id` plus URL, calls a verifier implementation, and dispatches `app.workers.youtube.run_youtube_verification_task.delay(...)` for async work (`backend/app/goal_types/youtube_video/__init__.py`).

## Current limitation
Plugin discovery is not yet the only source of truth. `GoalCreate` still hard-codes the allowed goal type names, so adding or generating a plugin does not automatically make creation requests valid without further backend changes (`backend/app/schemas/goal.py`, `backend/app/goal_types/registry.py`).

## Why it matters for D010
For chat-factory generation, this plugin seam is the most obvious landing point for generated behavior, sample prompts, and criteria schemas. But the D010 diagram and docs should also show the extra work outside the plugin layer: create-time validation, frontend goal creation UX, and any missing proof/camera infrastructure (`backend/app/schemas/goal.py`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`).

## Files read
- `backend/app/goal_types/base.py`
- `backend/app/goal_types/registry.py`
- `backend/app/goal_types/youtube_video/__init__.py`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`
- `backend/app/core/celery_app.py`
