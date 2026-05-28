# goal-type-plugin-contract

## Discovery contract that is actually test-backed
`backend/app/goal_types/registry.py` walks subpackages under `backend/app/goal_types/`, imports each package, and registers it when the package exposes a module-level `goal_type` object that is an instance of `GoalTypeBase` (`backend/app/goal_types/registry.py`).

`backend/tests/test_goal_type_smoke.py` proves the minimal discovery shape today:
- a new subdirectory under `backend/app/goal_types/`
- `__init__.py`
- `definition.py`
- `verifier.py`
- a module-level `goal_type` instance, either via a concrete subclass or `_DynamicGoalType`

That smoke test is the strongest current evidence for what a generator can emit without touching a central registry file.

## Abstract plugin API
`GoalTypeBase` defines the metadata and hook surface:
- `name`
- `description`
- `sample_prompts`
- `criteria_schema`
- `async verify(proof_data, criteria_data)` — required
- `submit_proof(...)` — optional override
- `dispatch_verification(...)` — optional override (`backend/app/goal_types/base.py`)

The built-in `youtube_video` and `api_endpoint` plugins follow that pattern by importing `definition`, implementing a concrete class, exposing `goal_type`, and overriding `submit_proof()`, `verify()`, and `dispatch_verification()` (`backend/app/goal_types/youtube_video/__init__.py`, `backend/app/goal_types/api_endpoint/__init__.py`).

## Route contract that the app actually exercises
The current proof route is narrower and more concrete than the abstract class:
1. `routes/goals.py` loads the goal's plugin with `goal_type_registry.get_type(goal.goal_type)`.
2. It flattens the `ProofSubmissionCreate` body with `model_dump(exclude_unset=True)`.
3. It calls `await goal_type.verify(proof_data, criteria_data)` directly.
4. If the plugin returns `verification_status == "rejected"`, the route returns that rejection immediately and does not create a `ProofSubmission` row.
5. Otherwise it stores the flattened `proof_data` in `ProofSubmission.proof_data` with a pending status.
6. After persistence, it best-effort calls `dispatch_verification()` if the plugin exposes one (`backend/app/routes/goals.py`, `backend/app/models/proof.py`, `backend/tests/test_goal_dispatch.py`).

## Important mismatches and constraints
- `submit_proof()` exists in the abstract API and in concrete plugins, but the live route does not use it.
- `ProofSubmissionCreate` only models URL/API/repo/token-style JSON fields; there is no media asset or file-upload field (`backend/app/schemas/proof.py`).
- `get_celery_include_modules()` derives `app.workers.<goal_type>`, but the concrete built-ins dispatch to `app.workers.youtube` and `app.workers.api_check` (`backend/app/goal_types/registry.py`, `backend/app/goal_types/youtube_video/__init__.py`, `backend/app/goal_types/api_endpoint/__init__.py`).

## Practical guidance for generated plugins
- Make the package name, `goal_type.name`, and any creation-time string values line up exactly.
- Emit `definition.py` metadata because `/api/goal-types` returns that metadata to clients (`backend/app/routes/goals.py`).
- Make `verify()` capable of handling the flattened route payload that comes from `ProofSubmissionCreate` today.
- Provide an explicit `dispatch_verification()` if async work matters; do not rely only on inferred worker-module naming.
- If the generated type must be user-selectable in the current product, also update `GoalCreateScreen`, `GoalCreate`, and the SQLAlchemy enums in `models/goal.py`; the registry alone is not enough.
