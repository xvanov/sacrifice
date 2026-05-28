# sacrifice-chat

## What this module name means here
`Sacrifice-chat` is a directional name for prompt-to-goal matching work. There is no dedicated chat module, chat screen, or backend chat route in the files read. The current app is still navigated through handwritten screens and typed forms (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`).

## Current live surfaces
- `frontend/App.tsx` conditionally renders `HomeScreen`, `GoalCreateScreen`, `GoalDetailScreen`, proof screens, dashboard, notifications, and login. No chat screen is mounted.
- `frontend/hooks/useNavigation.tsx` defines the screen union. It includes `home`, `dashboard`, `goal-create`, `goal-detail`, proof submission screens, `notifications`, and `login`; there is no conversational route.
- `frontend/screens/GoalCreateScreen.tsx` is the current intake flow. The user explicitly picks one of four goal types and fills a goal-type-specific form.
- `backend/app/routes/goals.py` exposes `GET /api/goal-types`, which returns plugin metadata such as `name`, `description`, `sample_prompts`, and `criteria_schema`. That metadata is the closest existing backend input for a future matcher.

## What is true today
The repo does not yet translate free-form user prompts into generated or selected goal types. Current intake is form-first and enum-backed, not chat-first. Even though backend plugins expose `sample_prompts`, the frontend does not currently consume `/api/goal-types` to drive a conversational or dynamic creation experience (`backend/app/routes/goals.py`, `frontend/screens/GoalCreateScreen.tsx`).

## Generator-direction implications
- A prompt matcher would be new product surface area, not a small tweak to existing navigation.
- The most reusable backend artifact already present is goal-type metadata from the registry.
- Any chat-driven flow still has to terminate in the same creation constraints that currently accept only `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- For a novel camera-backed case such as `pushup_counter`, chat alone is insufficient because proof capture and upload infrastructure are also missing (`frontend/app.json`, `frontend/services/api.ts`, `backend/app/schemas/proof.py`).
