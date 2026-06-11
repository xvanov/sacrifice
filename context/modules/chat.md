# chat

## What this module is
Chat-driven goal creation is the primary entry point for creating goals in Sacrifice. The backend exposes chat session and message endpoints in `backend/app/routes/chat.py` with goal-type matching via `backend/app/services/chat_match.py`. The frontend chat screen (`ChatGoalCreateScreen`) is delivered separately; this module currently covers only the backend HTTP surface.

## Files read
- `backend/app/main.py`
- `backend/app/routes/chat.py`
- `backend/app/models/chat_session.py`
- `backend/app/services/chat_match.py`
- `backend/app/config.py`
- `backend/app/schemas/goal.py`

## Public shape now
The backend chat surface includes:
- `POST /api/chat/sessions` — creates a new chat session with the greeting message (`backend/app/routes/chat.py`)
- `POST /api/chat/sessions/{session_id}/messages` — posts a user turn, runs goal-type matching against the D007 registry, and returns an assistant response with a structured `action` payload (match_proposed, no_match) per `api_spec.md` (`backend/app/routes/chat.py`)
- `POST /api/chat/sessions/{session_id}/request-new-goal-type` — stubbed, returns 501 (`backend/app/routes/chat.py`)

## Notable current behaviors
- FastAPI mounts the chat router at `/api/chat` alongside health, auth, dashboard, goals, notifications, and payment routers (`backend/app/main.py`).
- The `chat_sessions` table persists sessions with `messages` (JSONB), `draft_goal` (JSONB), and `status` (`active`, `goal_created`, `awaiting_goal_type`) (`backend/app/models/chat_session.py`).
- Session ownership is enforced: accessing a session belonging to another user returns 403, while nonexistent sessions return 404 (`backend/app/routes/chat.py`).
- Goal-type matching uses one LLM call per chat turn via `chat_match.match()`, which builds a catalog from the D007 registry and calls the configured Azure Foundry model (`backend/app/services/chat_match.py`).
- Match confidence threshold and model id are configurable via `chat_match_confidence_threshold` (default 0.7) and `chat_match_model_id` (`backend/app/config.py`).
- Transient LLM failures return 502 and persist a retry-friendly assistant message so the session is compatible with the frontend retry card flow (`backend/app/routes/chat.py`).
- The `request-new-goal-type` endpoint is stubbed (501) pending D010 (`backend/app/routes/chat.py`).

## Change guidance
New chat behavior should extend `backend/app/routes/chat.py` for HTTP surface changes and `backend/app/services/chat_match.py` for matching logic. The message-turn processing and goal-type matching logic (D009 match behavior) builds on top of this create-session foundation.
