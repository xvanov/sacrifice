# chat

## What this module is
Chat-driven goal creation is the primary entry point for creating goals in Sacrifice. The backend exposes chat session endpoints (`backend/app/routes/chat.py`) and the frontend provides `ChatGoalCreateScreen` for the conversational UI (`frontend/screens/ChatGoalCreateScreen.tsx`). The legacy form-based `GoalCreateScreen` has been removed in favor of this chat-driven flow.

## Files read
- `backend/app/main.py`
- `backend/app/routes/chat.py`
- `backend/app/models/chat_session.py`
- `frontend/hooks/useNavigation.tsx`
- `frontend/App.tsx`
- `frontend/services/api.ts`
- `frontend/screens/HomeScreen.tsx`
- `frontend/screens/ChatGoalCreateScreen.tsx`
- `backend/app/schemas/goal.py`

## Public shape now
The chat surface includes:
- `POST /api/chat/sessions` — creates a new chat session with the greeting message (`backend/app/routes/chat.py`)
- `POST /api/chat/sessions/{session_id}/request-new-goal-type` — stubbed, returns 501 (`backend/app/routes/chat.py`)
- `frontend/screens/ChatGoalCreateScreen.tsx` — conversational screen with message list, text input, and structured action cards (match proposed, no match, awaiting input, ready to create) per the D009 `api_spec.md`
- The frontend navigation map includes `goal-create-chat` mapping to `ChatGoalCreateScreen` (`frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`)
- The shared API client exposes `createChatSession`, `sendChatMessage`, `createGoalFromChat`, and `requestNewGoalType` (`frontend/services/api.ts`)

## Notable current behaviors
- FastAPI mounts the chat router at `/api/chat` alongside health, auth, dashboard, goals, notifications, and payment routers (`backend/app/main.py`).
- The `chat_sessions` table persists sessions with `messages` (JSONB), `draft_goal` (JSONB), and `status` (`active`, `goal_created`, `awaiting_goal_type`) (`backend/app/models/chat_session.py`).
- Session ownership is enforced: accessing a session belonging to another user returns 403, while nonexistent sessions return 404 (`backend/app/routes/chat.py`).
- The frontend home screen navigates to `goal-create-chat` when the user taps "Create goal" (`frontend/screens/HomeScreen.tsx`).
- The `request-new-goal-type` endpoint is stubbed (501) pending D010 (`backend/app/routes/chat.py`).

## Change guidance
New chat behavior should extend `backend/app/routes/chat.py` for backend changes and `frontend/screens/ChatGoalCreateScreen.tsx` for frontend changes. The message-turn processing and goal-type matching logic (D009 match behavior) builds on top of this create-session foundation.
