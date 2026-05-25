# Frontend module

## Scope
This module is the Expo application in `frontend/`. It renders login, dashboard, goal creation, goal detail, notification, and proof-submission flows for web and native targets from one TypeScript codebase (`frontend/package.json`, `frontend/App.tsx`).

## Entry points
- `frontend/App.tsx` — app shell, font loading, auth gate, and screen selection.
- `frontend/hooks/useAuth.tsx` — session restore, redirect callback handling, OAuth entry points, and email auth completion.
- `frontend/hooks/useNavigation.tsx` — in-memory screen state and back-stack handling.
- `frontend/services/api.ts` — backend REST wrapper used by the screens.

## Public surface
The app currently renders screens for:
- home
- dashboard
- goal creation
- goal detail
- YouTube proof submission
- API endpoint proof submission
- dev sandbox proof submission
- notifications
- login

The API client calls health, goals, verification status, dashboard, notifications, payment methods, setup intents, and charity search endpoints (`frontend/services/api.ts`).

## State and configuration
- The frontend uses `EXPO_PUBLIC_API_URL` when present and otherwise talks to `http://localhost:8000` (`frontend/services/api.ts`).
- Authentication state lives in `AuthProvider`; screen state lives in `NavigationProvider` (`frontend/hooks/useAuth.tsx`, `frontend/hooks/useNavigation.tsx`).
- The repo includes an agent note to use the exact Expo SDK 54 documentation when changing frontend behavior (`frontend/AGENTS.md`).

## Current constraints
- Navigation is custom and in-memory, so there is no URL-driven router state inside the app shell (`frontend/hooks/useNavigation.tsx`).
- The frontend removes its saved token on backend `401` responses and then relies on auth restoration or re-login (`frontend/services/api.ts`, `frontend/hooks/useAuth.tsx`).
- Runtime screen composition is centralized in a single `App.tsx` switch-like component, so new screens require edits there (`frontend/App.tsx`).
