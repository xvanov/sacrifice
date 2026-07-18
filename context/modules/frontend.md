# Frontend module

## Purpose
The frontend module is an Expo app that gates the product behind authentication, then renders dashboard, goal creation/detail, proof submission, notifications, and payment-method screens (`frontend/App.tsx`).

## Entry points and shape
- `frontend/App.tsx` wraps the app in `AuthProvider` and `NavigationProvider`, shows `LoginScreen` when unauthenticated, and otherwise routes to the current screen.
- `frontend/hooks/useAuth.tsx` owns the app-level auth state and delegates login/logout/storage behavior to the auth service.
- `frontend/services/api.ts` performs HTTP calls, attaches the current bearer token, and clears local auth state when the API reports `401`.
- `frontend/screens/LoginScreen.tsx` presents email/password, Google, and GitHub entry points and surfaces provider-conflict messages from backend auth responses.

## Auth relevance
This module is where bearer material becomes user session state on device or in the browser. The auth service stores the token in SecureStore on native and browser storage on web, then the API helper reuses it on every authenticated request (`frontend/services/auth.ts`, `frontend/services/api.ts`). OAuth login uses the app scheme `sacrifice` for native redirect handling (`frontend/app.json`, `frontend/services/auth.ts`).

## Current constraints
- Frontend work should follow Expo SDK 54 guidance, per `frontend/AGENTS.md` and `frontend/package.json`.
- The app currently uses a custom in-app navigation context rather than React Navigation (`frontend/App.tsx`).
- Session expiry handling is reactive: the API client clears auth state after a `401`, which forces the user back to the login surface (`frontend/services/api.ts`, `frontend/hooks/useAuth.tsx`).
