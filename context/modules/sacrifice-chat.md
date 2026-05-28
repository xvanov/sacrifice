# sacrifice-chat

## What this slice is
`Sacrifice` does not currently have a dedicated chat module in the frontend or backend files read for this D010 scan. The closest current user entrypoint is the typed create-goal flow wired through the Expo app shell, local navigation state, and the shared JSON API client (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`).

## Current state
- `App.tsx` renders screens by matching `currentScreen.name`, and the available names are `home`, `dashboard`, `goal-create`, `goal-detail`, `proof-submission`, `api-endpoint-proof-submission`, `dev-sandbox-proof-submission`, `notifications`, and `login` (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).
- The API client has goal, proof, dashboard, notification, payment, and charity helpers, but no chat request helper, transcript submission helper, or generator endpoint wrapper (`frontend/services/api.ts`).
- FastAPI mounts health, auth, dashboard, goal-types, goals, notifications, and payment routers only; no chat router appears in the current backend composition (`backend/app/main.py`).
- `POST /api/goals` expects `goal_type` and `criteria` to already be chosen before the request reaches the backend, so any future chat matcher or generator has to resolve down to that existing contract or change it (`backend/app/routes/goals.py`, `backend/app/schemas/goal.py`).

## Why it matters for D010
The architecture diagram for chat factory generation should show `chat` as a missing integration seam, not as an implemented subsystem. The current code path still routes a user from the app shell into `GoalCreateScreen`, and the backend still receives a conventional create-goal payload rather than a conversational transcript (`frontend/App.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `backend/app/routes/goals.py`).

## Files read
- `frontend/App.tsx`
- `frontend/hooks/useNavigation.tsx`
- `frontend/screens/GoalCreateScreen.tsx`
- `frontend/services/api.ts`
- `backend/app/main.py`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`
