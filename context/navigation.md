# Navigation

## When working on the D010 architecture diagram for chat factory generation flow
- Read first:
  - `context/project.md` — slow-changing repo shape and high-level D010 constraints
  - `context/current-state.md` — the current typed goal flow and the missing chat path
  - `context/architecture-diagrams.md` — current system and create/verify sequence diagrams
  - `context/modules/sacrifice-chat.md` — what chat does not exist yet in the current app shell
  - `context/modules/sacrifice-backend.md` — backend boundaries the diagram must terminate at
  - `context/modules/goal-type-plugins.md` — plugin seam and current hard-coded limits
  - `context/modules/factory-directions.md` — PRD / prompt / activity inputs that frame the work
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/services/api.ts`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `backend/app/main.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/goal_types/registry.py`
  - `backend/app/core/celery_app.py`

## When working on frontend goal entry or a future chat entry surface
- Read first:
  - `context/current-state.md` — explains the present screen-driven creation path
  - `context/modules/sacrifice-chat.md` — documents the current absence of a chat route
  - `context/modules/camera-capture-pipeline.md` — shows why phone-camera proof is a larger cross-cutting gap
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `frontend/AGENTS.md`

## When working on backend goal creation, proof submission, or generator landing points
- Read first:
  - `context/current-state.md` — current create-time and proof-time decisions
  - `context/modules/sacrifice-backend.md` — route and schema boundaries
  - `context/modules/goal-type-plugins.md` — plugin contract and registry mechanics
- Relevant code paths:
  - `backend/app/main.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/schemas/proof.py`
  - `backend/app/goal_types/base.py`
  - `backend/app/goal_types/registry.py`
  - `backend/app/goal_types/youtube_video/__init__.py`
  - `backend/app/core/celery_app.py`

## When working on goal-type generation or plugin regression coverage
- Read first:
  - `context/current-state.md` — explains why plugin generation is only part of the work
  - `context/modules/goal-type-plugins.md` — concrete plugin contract and registry path
  - `context/modules/factory-directions.md` — repository docs that currently frame implementation work
- Relevant code paths:
  - `backend/app/goal_types/base.py`
  - `backend/app/goal_types/registry.py`
  - `backend/app/goal_types/youtube_video/__init__.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`

## When working on camera capture, mobile proof, or pushup-style verification inputs
- Read first:
  - `context/project.md` — repo-wide capture constraints
  - `context/current-state.md` — current missing upload and capture boundaries
  - `context/modules/camera-capture-pipeline.md` — the specific client/backend gaps
- Relevant code paths:
  - `frontend/package.json`
  - `frontend/app.json`
  - `frontend/AGENTS.md`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `backend/app/schemas/proof.py`
  - `backend/app/routes/goals.py`
