# Navigation

## When working on prompt-to-goal matching or a future chat intake
- Read these context files first:
  - `context/project.md`
  - `context/current-state.md`
  - `context/modules/sacrifice-chat.md`
  - `context/modules/goal-type-plugin-contract.md`
- Then inspect these live files:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `backend/app/routes/goals.py`
  - `backend/app/goal_types/registry.py`

## When working on backend goal-type generation or registry compatibility
- Read these context files first:
  - `context/project.md`
  - `context/current-state.md`
  - `context/modules/sacrifice-backend.md`
  - `context/modules/goal-type-plugin-contract.md`
  - `context/modules/factory-direction-runner.md`
- Then inspect these live files:
  - `backend/app/goal_types/base.py`
  - `backend/app/goal_types/registry.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`
  - `backend/tests/test_goal_type_smoke.py`
  - `backend/tests/test_goal_type_registry.py`
  - `backend/tests/test_goal_dispatch.py`

## When working on the factory direction runner or repo orchestration
- Read these context files first:
  - `context/project.md`
  - `context/current-state.md`
  - `context/modules/factory-direction-runner.md`
  - `context/modules/sacrifice-backend.md`
- Then inspect these live files:
  - `PROMPT.md`
  - `activity.md`
  - `opencode.json`
  - `backend/app/config.py`

## When working on phone-camera proofs or the pushup-style physical goal path
- Read these context files first:
  - `context/project.md`
  - `context/current-state.md`
  - `context/modules/camera-capture-pipeline.md`
  - `context/modules/sacrifice-backend.md`
  - `context/modules/goal-type-plugin-contract.md`
- Then inspect these live files:
  - `frontend/AGENTS.md`
  - `frontend/app.json`
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `backend/app/schemas/proof.py`
  - `backend/app/routes/goals.py`
  - `backend/app/models/proof.py`

## When validating whether a generated goal type can work end-to-end today
- Read these context files first:
  - `context/current-state.md`
  - `context/modules/sacrifice-chat.md`
  - `context/modules/sacrifice-backend.md`
  - `context/modules/goal-type-plugin-contract.md`
  - `context/modules/camera-capture-pipeline.md`
- Then inspect these live files:
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/services/api.ts`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`
  - `backend/app/schemas/proof.py`
  - `backend/app/routes/goals.py`
  - `backend/tests/test_goal_type_smoke.py`
  - `backend/tests/test_goal_dispatch.py`
