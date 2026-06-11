# Navigation

## When working on chat-driven goal creation end to end
- Context files:
  - `context/current-state.md` — current creation coupling, plugin-catalog availability, and missing matcher surface
  - `context/modules/frontend.md` — where the typed creation UI and client request helpers live today
  - `context/modules/backend.md` — where plugin discovery, goal persistence, and any future matcher endpoint belong
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/services/api.ts`
  - `frontend/__tests__/screens/GoalCreateScreen.test.tsx`
  - `backend/app/main.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/services/goal.py`
  - `backend/app/models/goal.py`
  - `backend/app/goal_types/registry.py`
  - `backend/app/services/llm.py`
  - `backend/tests/test_goal_types_api.py`

## When working on the frontend replacement for typed goal creation
- Context files:
  - `context/current-state.md` — which assumptions are currently encoded in the screen and tests
  - `context/modules/frontend.md` — screen structure, navigation constraints, and API-client gaps
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/services/api.ts`
  - `frontend/__tests__/screens/GoalCreateScreen.test.tsx`
  - `frontend/AGENTS.md`

## When working on backend goal matching against the existing plugin catalog
- Context files:
  - `context/current-state.md` — current registry-backed catalog and the missing prompt matcher
  - `context/modules/backend.md` — route surface, persistence constraints, and LLM/helper boundaries
- Relevant code paths:
  - `backend/app/main.py`
  - `backend/app/routes/goals.py`
  - `backend/app/goal_types/registry.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/services/goal.py`
  - `backend/app/models/goal.py`
  - `backend/app/services/llm.py`
  - `backend/tests/test_goal_types_api.py`

## When preserving proof submission while creation changes
- Context files:
  - `context/current-state.md` — proof submission is still downstream of creation and remains separate in this batch
  - `context/modules/frontend.md` — app-shell routing and client helpers that must keep working after creation changes
  - `context/modules/backend.md` — goal-type-backed proof dispatch that should stay stable while creation changes
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/services/api.ts`
  - `backend/app/routes/goals.py`

<!-- factory:context-refresh ts=2026-06-11T23:08:35.417882+00:00 after_pr=#115 -->
