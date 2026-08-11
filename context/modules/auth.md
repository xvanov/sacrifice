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
- Password reset **is implemented** — `POST /api/auth/password/reset/request`
  (`backend/app/routes/auth.py:728`) and `POST /api/auth/password/reset/confirm`
  (`auth.py:751`), with single-use JTI binding (`backend/app/models/reset_token_jti.py`),
  a 30-minute purpose-scoped token (`RESET_TOKEN_EXPIRE_MINUTES`,
  `backend/app/services/auth.py:34`) and post-reset session revocation (`auth.py:809`).
  What it lacks is **delivery**: the token is minted into a discarded local
  (`_token = create_reset_token(...)`, `auth.py:746`) and there is no email transport
  anywhere in `backend/app`. The flow is therefore unusable end-to-end while every
  endpoint it needs already exists.
- Email **verification** is genuinely absent: registration issues a session with no
  mailbox proof, there is no verification-token issuance or consumption, and no route
  under `/api/auth/**` verifies an address. `email_verified` appears only as a claim
  read back from an OAuth provider (`backend/app/services/auth.py`), which says nothing
  about email/password signups.
  <!-- CORRECTED 2026-08-11. These two bullets replace a single bullet that claimed
  email auth lacked BOTH password reset and email verification. Password reset had
  shipped (factory direction 113 / story 138) more than a week earlier, and that
  sentence is why the scheduled `security` persona re-filed it as factory direction
  126 on 2026-08-10. PR #382 corrected the identical claim in
  `context/modules/security.md` and missed this file. The old wording is deliberately
  NOT quoted verbatim here — a regression guard in `software-factory`
  (`tests/test_direction_route_premise_guard.py`) greps for it, and reproducing it in
  a comment would defeat that guard; `git log -p` has the exact text. Check
  `grep -n '@router\.' backend/app/routes/*.py` before writing "lacks" about an
  endpoint. -->
- `POST /api/auth/logout` (`auth.py:714`) and `POST /api/auth/refresh` (`auth.py:702`)
  both rotate `users.auth_session_id`, and there is exactly one such marker per user —
  so logout is already a logout-all and refresh already rotates on use. Neither is a
  missing capability.
- The CLI persists the access token without additional encryption at rest in the user's config file (`backend/cli/client.py`).
