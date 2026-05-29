# goal-creation

## What this module is
Goal creation is now chat-driven via `ChatGoalCreateScreen` (`frontend/screens/ChatGoalCreateScreen.tsx`). The user taps `+ New` on the home screen, navigation switches to `goal-create-chat`, and a conversational interface backed by `POST /api/chat/sessions` guides the user through goal-type matching and criteria collection before posting to `POST /api/goals` (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`, `frontend/screens/ChatGoalCreateScreen.tsx`, `backend/app/routes/chat.py`, `backend/app/routes/goals.py`).

The legacy form-based `GoalCreateScreen` (`frontend/screens/GoalCreateScreen.tsx`) has been removed. It was a typed, frontend-orchestrated form with a hard-coded `GoalType` picker for `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`.

## Files read
- `frontend/screens/HomeScreen.tsx`
- `frontend/hooks/useNavigation.tsx`
- `frontend/App.tsx`
- `frontend/screens/ChatGoalCreateScreen.tsx`
- `frontend/services/api.ts`
- `backend/app/routes/goals.py`
- `backend/app/routes/chat.py`
- `backend/app/schemas/goal.py`
- `backend/app/models/goal.py`

## Public shape
`ChatGoalCreateScreen` provides a conversational interface that:
- creates a chat session on mount via `POST /api/chat/sessions`
- presents a message list with role-styled chat bubbles
- renders structured assistant affordances as action cards: match proposed ("Use this" / "Try another approach"), no match ("Yes, build it" / "Let me rephrase"), awaiting input, and ready to create ("Create goal" / "Edit")
- posts messages via `POST /api/chat/sessions/{session_id}/messages`
- creates goals via `POST /api/chat/sessions/{session_id}/create-goal`
- navigates to `goal-detail` when creation succeeds (`frontend/screens/ChatGoalCreateScreen.tsx`, `frontend/services/api.ts`).

On the backend, `POST /api/goals` expects `goal_type` and `criteria` to already be resolved. `GoalCreate` validates the allowed goal types, and the database model persists the type and criteria using enums rather than a dynamic registry (`backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).

## Notable current behaviors
- The goal-type classifier is now in the chat backend rather than the UI (`backend/app/routes/goals.py`).
- Home screen navigation now routes to the `goal-create-chat` screen; there is no more `goal-create` screen (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`).
- Proof submission stays separate from creation. The frontend still has distinct proof submission screens, and the backend still routes proof by stored `goal_type` after creation (`frontend/App.tsx`, `backend/app/routes/goals.py`).

## Change guidance
If a task modifies chat-driven creation, start in `frontend/screens/ChatGoalCreateScreen.tsx` and `backend/app/routes/chat.py`. The legacy form-based `GoalCreateScreen` has been removed. Keep proof submission screens and proof-routing logic out of scope unless the task explicitly changes proof handling (`frontend/screens/ChatGoalCreateScreen.tsx`, `backend/app/routes/chat.py`, `frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `frontend/App.tsx`).
