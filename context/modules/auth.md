# Auth module

## Purpose
The auth module handles Google OAuth, GitHub OAuth, email/password login, bearer issuance, current-user resolution, logout, and OAuth auth-code exchange across backend, frontend, and CLI surfaces (`backend/app/routes/auth.py`, `backend/app/services/auth.py`, `frontend/services/auth.ts`, `backend/cli/client.py`).

## Backend flow
- `/api/auth/google/login` and `/api/auth/github/login` mint a fresh state value, optionally encode mobile redirect context into state, and set an `oauth_state` cookie before redirecting to the provider (`backend/app/routes/auth.py`).
- Callback routes verify state, exchange the provider code, resolve or create the user, store a pending auth code, and redirect the caller with `auth_code` rather than an access token (`backend/app/routes/auth.py`).
- `/api/auth/exchange` consumes that one-time code, creates the active bearer token, and returns the user payload; replaying the same code is rejected in tests (`backend/tests/test_auth.py`).
- Email auth endpoints register and log in users directly, while returning explicit `account_exists` conflicts when an email belongs to a different provider (`backend/tests/test_email_auth.py`).

## Token lifecycle
The auth service creates JWT access tokens and includes claims that let the dependency layer bind the token to the user's active server-side session. The current-user dependency rejects tokens with missing or wrong purpose, unknown user ids, or stale session identifiers (`backend/app/services/auth.py`, `backend/app/core/dependencies.py`). Logging in again, exchanging a new auth code, refreshing the session, or logging out rotates the active auth-session marker on the user row, so previously issued tokens stop working even if their JWT expiry has not passed (`backend/app/services/auth.py`, `backend/app/models/user.py`).

## Frontend and CLI clients
The Expo auth service starts the browser/native OAuth flow, consumes the returned auth code, persists the bearer locally, and exposes helpers through `useAuth` (`frontend/services/auth.ts`, `frontend/hooks/useAuth.tsx`). The CLI opens the browser to the backend login flow, receives the callback on a local port, exchanges the auth code, and saves the returned bearer for later commands (`backend/cli/main.py`, `backend/cli/client.py`).

## Current constraints
- Browser/mobile OAuth handoff is intentionally code-based to keep raw bearer tokens out of redirect URLs (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- Email auth currently lacks the additional account lifecycle features a production auth system would normally have, such as password reset and email verification, in the inspected code path (`backend/app/routes/auth.py`, `backend/tests/test_email_auth.py`).
- The CLI persists the access token without additional encryption at rest in the user's config file (`backend/cli/client.py`).
