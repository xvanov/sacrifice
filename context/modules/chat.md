# chat

## What this module is
There is no dedicated chat module in the current code paths read for Sacrifice. The repository's current goal-entry flow still routes directly from the home screen to the typed `GoalCreateScreen`, and the backend exposes no mounted chat router or chat-specific client method (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`, `backend/app/main.py`, `frontend/services/api.ts`).

## Files read
- `backend/app/main.py`
- `frontend/hooks/useNavigation.tsx`
- `frontend/App.tsx`
- `frontend/services/api.ts`
- `frontend/screens/HomeScreen.tsx`
- `frontend/screens/GoalCreateScreen.tsx`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`

## Public shape now
The closest current surfaces to a future chat flow are:
- the home-screen `+ New` entrypoint that navigates into goal creation (`frontend/screens/HomeScreen.tsx`)
- the screen switch in `App.tsx` and the `Screen` union in `useNavigation.tsx`, which currently have no `chat` screen (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`)
- the shared API client, which currently exposes goal CRUD, proof submission, dashboard, notifications, payments, and charity search, but no chat or goal-type matching call (`frontend/services/api.ts`)
- the goal creation endpoint, which expects `goal_type` and typed `criteria` to be known before the request is sent (`backend/app/routes/goals.py`, `backend/app/schemas/goal.py`)

## Notable current behaviors
- FastAPI currently mounts health, auth, dashboard, goals, notifications, and payment routers only; there is no mounted chat router in `main.py` (`backend/app/main.py`).
- The frontend navigation map contains `home`, `dashboard`, `goal-create`, `goal-detail`, proof submission screens, `notifications`, and `login`; there is no chat route in the current screen union (`frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`).
- The frontend API layer does not fetch a goal-type catalog, run prompt matching, or submit a chat transcript. It only posts fully formed goal payloads to `/api/goals` (`frontend/services/api.ts`).
- Because the backend goal schema validates a fixed set of goal types, any chat-based matcher still has to end in one of the currently allowed enum values unless the backend type system changes too (`backend/app/schemas/goal.py`, `backend/app/routes/goals.py`).

## Change guidance
A new chat surface will need changes on both sides of the boundary. On the frontend, start with the home-screen entrypoint, navigation screen map, `App.tsx` switch, and API client. On the backend, start with router composition in `main.py` and decide whether matching belongs in a new chat route or inside the goal surface before changing request validation. Do not assume a registry or catalog endpoint already exists; no such surface appeared in the files read for this scan (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`, `frontend/services/api.ts`, `backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`).
