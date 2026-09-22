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
- Email/password auth has **no email-verification gate**: `POST /api/auth/email/register`
  issues a session with no mailbox proof, and nothing withholds sensitive operations
  from an unverified account (`backend/app/routes/auth.py:571`). This is the one gap
  of the three this bullet used to claim.
  <!-- CORRECTED 2026-08-11. This bullet previously claimed THREE absences: rate
  limiting, a password-reset flow, and an email-verification gate. Two of the three
  were already SHIPPED. Rate limiting: `check_auth_rate_limit`
  (`backend/app/core/rate_limiter.py`) is a dependency on every route in
  `backend/app/routes/auth.py`. Password reset:
  `POST /api/auth/password/reset/request` (`auth.py:728`) and
  `POST /api/auth/password/reset/confirm` (`auth.py:751`), shipped by factory
  direction 113 / story 138.
  The cost of the stale version: the scheduled `security` persona reads this file and
  re-filed password reset as factory directions d094, d098, d108, d113, 118 and again
  as 126 on 2026-08-10 — six times, five of them after it shipped. PR #382 corrected
  the same false claim in `context/modules/security.md` and missed this file and
  `context/modules/auth.md`.
  The old wording is deliberately NOT quoted verbatim: a regression guard in
  `software-factory` (`tests/test_direction_route_premise_guard.py`) greps these docs
  for it, and reproducing it in a comment would defeat the guard. `git log -p` has the
  exact text. Before re-adding a "missing endpoint" claim here, check the route table:
  `grep -n '@router\.' backend/app/routes/*.py`. -->
- Session revocation is **global per user, already**: there is a single
  `users.auth_session_id` marker, so `POST /api/auth/logout` (`auth.py:714`) is a
  logout-all, and a completed password reset rotates the same marker
  (`auth.py:809`), invalidating every pre-reset token. A finding about session
  invalidation is a finding about these two handlers, not a request for new ones.
- Password reset **exists but does not deliver**: `password_reset_request` mints the
  token into a discarded local (`_token = create_reset_token(...)`, `auth.py:746`) and
  there is no email transport anywhere in `backend/app` (no smtp/sendgrid/mailgun/
  postmark/resend). The endpoint always answers `202`, and three tests certify it by
  asserting only the status code — a vacuously satisfied criterion. This is a real
  gap **behind an existing route**.

<!-- factory:context-refresh ts=2026-07-18T07:59:26.240512+00:00 after_pr=#224 -->
