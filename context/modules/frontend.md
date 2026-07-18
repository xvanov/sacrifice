# Frontend module

## Purpose
`frontend/` contains the single Expo-managed client for login, goal creation, goal history, proof submission, dashboard, and notifications (`frontend/App.tsx`, `frontend/package.json`).

## Entry points and public surfaces
- `frontend/App.tsx` loads fonts and global styles, restores auth state, initializes screen routing, and chooses between login, home, dashboard, goal creation/detail, proof submission, and notification screens.
- `frontend/screens/GoalCreateScreen.tsx` is the clearest description of the current goal-creation surface: it owns the built-in goal-type union, conditional criteria forms, charity search, payment-method warning, and submit behavior.
- `frontend/services/api.ts` is the transport layer for the app. It centralizes the API base URL, bearer token attachment, 401 cleanup, and all current JSON request helpers.
- `frontend/app.json` defines the managed Expo shell and currently enables only datetime picker, secure store, and web browser plugins.

## UX and state shape
- The app uses local providers for auth and navigation rather than a separate navigation package surfaced from the entry point (`frontend/App.tsx`).
- Goal creation currently supports four proof families: recorded video, API endpoint, dev sandbox, and GitHub repository conditions (`frontend/screens/GoalCreateScreen.tsx`).
- The creation form’s goal-type chooser is fully local today: `GOAL_TYPES` and `GOAL_TYPE_LABEL` define the labels and supported variants in the screen itself, even though the backend exposes richer type metadata through `/api/goal-types` (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/routes/goals.py`).
- Charity search is backed by API calls and stores the chosen Stripe Connect identifier on the form before goal submission (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`).
- Proof submission helpers post structured JSON bodies for each supported goal type, and verification polling reads a separate status endpoint (`frontend/services/api.ts`).

## Active constraints
- The goal-type union and labels are hardcoded in the screen, so the client does not yet build its form dynamically from `/api/goal-types` metadata (`frontend/screens/GoalCreateScreen.tsx`).
- The API wrapper is JSON-only and does not expose multipart uploads, file handles, or camera-capture transport (`frontend/services/api.ts`).
- The inspected Expo config does not currently install camera, media-library, or document-picker plugins (`frontend/app.json`).
- Repo guidance says frontend work should follow Expo 54’s versioned docs specifically, matching the declared `expo` dependency (`frontend/AGENTS.md`, `frontend/package.json`).

<!-- factory:context-refresh ts=2026-07-18T06:27:12.563273+00:00 after_pr=#217 -->
