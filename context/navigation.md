# Navigation

## When working on the camera capture pipeline direction
- Context files:
  - `context/project.md` — slow-changing repository shape and the high-level media constraint
  - `context/current-state.md` — current proof architecture, registry state, and missing upload pipeline
  - `context/modules/frontend.md` — app-shell, proof-screen, and API-client implications
  - `context/modules/backend.md` — backend HTTP, proof, and registry implications
  - `context/modules/mobile.md` — native plugin and permission implications
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `frontend/app.json`
  - `backend/app/main.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/proof.py`
  - `backend/app/goal_types/registry.py`

## When working on frontend proof UX or shared capture components
- Context files:
  - `context/current-state.md` — current proof-screen split and JSON-only transport
  - `context/modules/frontend.md` — where navigation, proof UI, and client helpers live today
  - `context/modules/mobile.md` — Expo runtime constraints for native capture
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `frontend/package.json`
  - `frontend/AGENTS.md`

## When working on backend upload endpoints or media contracts
- Context files:
  - `context/current-state.md` — current absence of upload primitives and partially dynamic goal typing
  - `context/modules/backend.md` — current backend surface and extension barriers
  - `context/project.md` — repo-level constraints and integrations
- Relevant code paths:
  - `backend/app/main.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/proof.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`
  - `backend/app/goal_types/registry.py`
  - `backend/app/core/celery_app.py`
  - `backend/app/config.py`

## When working on mobile permissions, plugins, or recorder viability
- Context files:
  - `context/project.md` — current client and native configuration summary
  - `context/current-state.md` — current mobile capability gaps
  - `context/modules/mobile.md` — Expo-managed native surface details
  - `context/modules/frontend.md` — app-shell entrypoints that will launch capture flows
- Relevant code paths:
  - `frontend/app.json`
  - `frontend/package.json`
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/AGENTS.md`

## When working on a new physical-world goal type
- Context files:
  - `context/current-state.md` — why media capture and upload should be introduced as shared infrastructure first
  - `context/modules/backend.md` — schema, model, registry, and proof-route touchpoints
  - `context/modules/frontend.md` — creation flow and proof-surface touchpoints
  - `context/modules/mobile.md` — native capability prerequisites
- Relevant code paths:
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`
  - `backend/app/schemas/proof.py`
  - `backend/app/routes/goals.py`
  - `backend/app/goal_types/registry.py`
