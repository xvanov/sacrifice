# Current State

## Active architectural decisions

Sacrifice runs as a FastAPI API plus an Expo client. The backend app wires auth, chat, dashboard, goal-type discovery, goals, notifications, payments, uploads, and webhooks into one process, and startup performs goal-type discovery before serving traffic so broken or tampered goal modules fail fast (`backend/app/main.py`).

Authentication is centralized across `backend/app/routes/auth.py`, `backend/app/services/auth.py`, and `backend/app/core/dependencies.py`. The backend accepts Google OAuth, GitHub OAuth, and email/password login. Protected routes resolve the current user through the shared bearer dependency, so one valid bearer token reaches multiple business surfaces rather than a narrow auth-only API (`backend/app/core/dependencies.py`, `backend/app/routes/goals.py`, `backend/app/routes/payment.py`).

OAuth callback handling is already shaped around replay defense. The backend sets an `oauth_state` cookie, verifies callback state on return, and redirects the browser or app with a one-time `auth_code` instead of a raw access token. The client then calls `/api/auth/exchange`, and the tests assert that reusing the same auth code fails with `401` (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`). The route logic also decodes CLI/mobile redirect context from state and only honors mobile redirect targets that pass the backend's safety checks (`backend/app/routes/auth.py`).

Bearer-token validity is session-bound rather than purely stateless. The auth service creates JWT access tokens, and the dependency layer checks token claims against the user's active auth session identifier stored on the user row. Logging in again, exchanging a fresh auth code, or logging out rotates that server-side session marker, which invalidates older bearer material even before token expiry (`backend/app/services/auth.py`, `backend/app/core/dependencies.py`, `backend/app/models/user.py`).

Client storage differs by surface. The Expo client stores the bearer through its auth service, using browser storage on web and SecureStore on native, then injects `Authorization: Bearer ...` on API calls (`frontend/services/auth.ts`, `frontend/hooks/useAuth.tsx`, `frontend/services/api.ts`). The CLI stores the bearer and user payload in plain JSON under `~/.config/sacrifice/config.json`, then reuses that token for every protected command (`backend/cli/client.py`).

Security-sensitive tokens stored at rest in the database are encrypted with Fernet. The crypto helper prefixes ciphertext with `fernet:` and keeps backward compatibility with legacy plaintext rows, using either `token_encryption_key` or a key derived from `jwt_secret` (`backend/app/core/crypto.py`, `backend/tests/test_crypto.py`).

## Module map

| Module | Purpose | Key files |
| --- | --- | --- |
| `backend` | Runs the FastAPI API and owns protected business routes. | `backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/routes/payment.py` |
| `frontend` | Presents login, dashboard, goal, and payment flows in Expo. | `frontend/App.tsx`, `frontend/hooks/useAuth.tsx`, `frontend/services/api.ts` |
| `auth` | Orchestrates provider login, email auth, bearer issuance, session invalidation, and auth-code exchange. | `backend/app/routes/auth.py`, `backend/app/services/auth.py`, `backend/app/core/dependencies.py`, `frontend/services/auth.ts` |
| `security` | Applies replay checks, token-at-rest encryption, and provider-conflict protections. | `backend/app/core/crypto.py`, `backend/tests/test_auth.py`, `backend/tests/test_email_auth.py`, `backend/tests/test_crypto.py` |
| `cli` | Supports local login and authenticated terminal workflows. | `backend/cli/main.py`, `backend/cli/client.py` |
| `migration` | Moves local environment and persisted state between machines. | `scripts/migration/bootstrap.sh`, `scripts/migration/bundle.sh` |

## Current constraints
- Bearer-token theft still directly enables account impersonation until the token expires or the session id rotates, because the same bearer unlocks goal changes, payment setup/history, notification state changes, uploads, and dashboard reads (`backend/app/routes/goals.py`, `backend/app/routes/payment.py`, `backend/app/core/dependencies.py`).
- OAuth replay protection is stronger than direct-token redirect flows, but it depends on state-cookie integrity and correct one-time auth-code handling (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- The CLI remains the weakest storage surface because it writes the access token to plaintext config under the user's home directory (`backend/cli/client.py`).
- The frontend is constrained by Expo SDK 54 guidance and the current app scheme `sacrifice`, which the auth flow relies on for native redirect handling (`frontend/AGENTS.md`, `frontend/app.json`, `frontend/services/auth.ts`).
- Email/password auth currently shows no built-in rate limit, no password reset flow, and no email-verification gate in the inspected surface (`backend/app/routes/auth.py`, `backend/tests/test_email_auth.py`).

<!-- factory:context-refresh ts=2026-07-18T11:47:12.694249+00:00 after_pr=#228 -->
