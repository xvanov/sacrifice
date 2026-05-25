# goal-creation

## What this module is
Goal creation is currently a typed, frontend-orchestrated feature surface rather than a backend-discovered workflow. The user taps `+ New` on the home screen, navigation switches to `goal-create`, and `GoalCreateScreen` gathers form state, charity selection, and type-specific criteria before posting directly to `POST /api/goals` (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `backend/app/routes/goals.py`).

## Files read
- `frontend/screens/HomeScreen.tsx`
- `frontend/hooks/useNavigation.tsx`
- `frontend/App.tsx`
- `frontend/screens/GoalCreateScreen.tsx`
- `frontend/services/api.ts`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`
- `backend/app/models/goal.py`

## Public shape
`GoalCreateScreen` keeps one `FormState` object with a hard-coded `goal_type` and conditional inputs for:
- `youtube_video`
- `api_endpoint`
- `dev_sandbox`
- `github_repo`

The screen also:
- checks whether payment methods exist with `api.getPaymentMethods()`
- performs debounced charity search with `api.searchCharities()`
- converts local input into a typed `criteria` payload
- posts the final payload through `api.createGoal()`
- navigates to `goal-detail` when creation succeeds (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`).

On the backend, `POST /api/goals` expects `goal_type` and `criteria` to already be resolved. `GoalCreate` validates the allowed goal types, and the database model persists the type and criteria using enums rather than a dynamic registry (`backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).

## Notable current behaviors
- The user must choose a goal type before filling the matching fields; the classifier lives in the UI, not in the backend (`frontend/screens/GoalCreateScreen.tsx`).
- The frontend currently builds different `criteria` payload shapes inline instead of fetching a goal-type catalog or plugin contract (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`).
- Home screen navigation treats creation as a dedicated screen named `goal-create`; there is no chat intermediary in the current screen map (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`).
- Proof submission stays separate from creation. The frontend still has distinct proof submission screens, and the backend still routes proof by stored `goal_type` after creation (`frontend/App.tsx`, `backend/app/routes/goals.py`).

## Change guidance
If a task replaces typed creation with chat, start here first. Trace the full path from `HomeScreen` and `useNavigation` into `GoalCreateScreen`, then through `frontend/services/api.ts` to `POST /api/goals`, and finally through the backend goal schema/model constraints. Keep proof submission screens and proof-routing logic out of scope unless the task explicitly changes proof handling (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `frontend/App.tsx`).
