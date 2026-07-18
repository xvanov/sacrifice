# Auth module

## What this module owns
The auth module covers how Sacrifice identifies a user, turns that identity proof into a bearer token, and invalidates old bearer material. In code, that behavior is split across the FastAPI auth router, JWT/session helpers, the shared `get_current_user(...)` dependency, the `User` model fields that hold revocation state, and the Expo auth/API helpers that persist and send the token (`backend/app/routes/auth.py`, `backend/app/services/auth.py`, `backend/app/core/dependencies.py`, `backend/app/models/user.py`, `frontend/services/auth.ts`, `frontend/services/api.ts`).

## Issuance paths
### Google and GitHub direct login APIs
- `POST /api/auth/google` accepts a Google ID token, verifies it through `verify_google_token(...)`, finds or creates the user, rotates `users.auth_session_id`, and returns `{access_token, user}` (`backend/app/routes/auth.py`, `backend/app/services/auth.py`).
- `POST /api/auth/github` accepts an OAuth code, exchanges it through GitHub, fetches `/user` and `/user/emails`, applies the same user-resolution logic, rotates `users.auth_session_id`, and returns `{access_token, user}` (`backend/app/routes/auth.py`, `backend/app/services/auth.py`).

### Browser / CLI / mobile OAuth callback flow
- `GET /api/auth/google/login` and `GET /api/auth/github/login` generate a random state nonce and set it in the `oauth_state` cookie for browser use (`backend/app/routes/auth.py`).
- CLI flows encode `cli|<port>|<state>` and mobile flows encode `mobile|<redirect_uri>|<state>` into the state parameter so the callback can route back to the originating surface (`backend/app/routes/auth.py`).
- `GET /api/auth/google/callback` and `GET /api/auth/github/callback` verify `state`, exchange the provider code, resolve the user, store a random `pending_auth_code_id`, mint a signed auth-exchange code, and redirect with `?auth_code=...` rather than the long-lived app bearer token (`backend/app/routes/auth.py`, `backend/app/services/auth.py`).
- `POST /api/auth/exchange` decodes that auth code and succeeds only when the embedded `code_id` still matches `users.pending_auth_code_id`; it then rotates the auth session and clears the pending code (`backend/app/routes/auth.py`, `backend/app/services/auth.py`, `backend/app/models/user.py`).

### Email/password and debug issuance
- `POST /api/auth/email/register` creates an `auth_provider="email"` user, hashes the password with bcrypt through `hash_password(...)`, rotates the auth session, and returns `{access_token, user}` (`backend/app/routes/auth.py`, `backend/app/core/passwords.py`).
- `POST /api/auth/email/login` looks up the email, verifies the bcrypt hash, rotates the auth session, and returns `{access_token, user}` on success (`backend/app/routes/auth.py`, `backend/app/core/passwords.py`).
- `GET /api/auth/dev/token` is a debug-only bypass guarded by `settings.debug`; it creates or reuses a dev user, rotates the auth session, and returns a bearer token for smoke-style local flows (`backend/app/routes/auth.py`, `backend/app/config.py`).

## Bearer-token shape and validation
- Access tokens are JWTs signed with `settings.jwt_secret` and `settings.jwt_algorithm` (`backend/app/config.py`, `backend/app/services/auth.py`).
- They include:
  - `sub`: user id
  - `exp`: expiry
  - `iat`: issuance time
  - `jti`: per-token random id
  - `purpose="access"`
  - `sid`: the current `users.auth_session_id` (`backend/app/services/auth.py`).
- `settings.jwt_expire_minutes` defaults to `60`, so expiry is time-based even if the user never logs out (`backend/app/config.py`).
- `get_current_user(...)` is the single backend gate for authenticated routes. It decodes the JWT, requires both `sub` and `sid`, loads the user row, and rejects the token with `401` if the row is gone or if `users.auth_session_id` no longer matches the token's `sid` (`backend/app/core/dependencies.py`).

## Rotation, logout, and replay defenses
### Session rotation
`rotate_auth_session(...)` writes a fresh UUID into `users.auth_session_id` and, by default, clears `users.pending_auth_code_id` before commit/refresh. That one database field is the revocation anchor for app bearer tokens (`backend/app/services/auth.py`, `backend/app/models/user.py`).

### Refresh and logout
- `POST /api/auth/refresh` requires a currently valid bearer token, rotates the auth session, and returns a new access token bound to the new `sid` (`backend/app/routes/auth.py`).
- `POST /api/auth/logout` also requires a currently valid bearer token and rotates the auth session again; the route returns `{"detail": "Logged out"}` and does not maintain a separate blacklist (`backend/app/routes/auth.py`).
- Backend tests verify that the old JWT receives `401` after refresh and after logout, while the replacement token still works (`backend/tests/test_auth.py`).

### Single-use auth codes
- Auth-exchange codes are distinct from access tokens: they use `purpose="auth_exchange"` and `AUTH_CODE_EXPIRE_SECONDS = 300` (`backend/app/services/auth.py`).
- The code is only accepted when its embedded `code_id` still matches `users.pending_auth_code_id`; a successful exchange rotates the session and clears the stored `code_id`, making the code single-use (`backend/app/routes/auth.py`, `backend/app/services/auth.py`).
- Backend tests cover the replay case by posting the same auth code twice and expecting the second call to fail with `401` (`backend/tests/test_auth.py`).

### OAuth CSRF and redirect handling
- Browser callback flows require the `oauth_state` cookie to match the returned state; missing or mismatched state yields `400 State mismatch` (`backend/app/routes/auth.py`).
- CLI/mobile flows cannot rely on the same cookie, so they carry the nonce inside the encoded `state`; mobile redirects are accepted only for `sacrifice://`, Expo schemes, or the configured frontend origin (`backend/app/routes/auth.py`).

## Account linking and takeover rules
`get_or_create_user(...)` deliberately treats a same-email login from a different provider as either a safe link or a conflict, depending on whether the incoming provider has actually proven ownership of that email (`backend/app/services/auth.py`).

- Linkable providers are limited to verified-email Google and verified-email GitHub (`backend/app/services/auth.py`).
- If a matching `(provider, provider_id)` row already exists, the service refreshes profile fields and keeps using that row (`backend/app/services/auth.py`).
- If the email already belongs to another provider:
  - verified Google/GitHub logins sign in to the existing row instead of silently relinking provider identity
  - unverified claims and the debug bypass are rejected with `AuthConflictError`, surfaced to clients as `409 {error:"account_exists", provider:"..."}` or as callback redirects with the same error details (`backend/app/routes/auth.py`, `backend/app/services/auth.py`).
- Tests cover the takeover regression, verified cross-provider linking, and the rule that an email/password account can be accessed through verified Google sign-in without changing its stored provider (`backend/tests/test_auth.py`).

## Client-side storage and transport
- The Expo auth helper keeps an in-memory `cachedToken` for fast access (`frontend/services/auth.ts`).
- On web, `setToken(...)` persists the bearer token in `localStorage` under `sacrifice_auth_token`; `removeToken(...)` clears it and also removes the chat goal-creation session key so one browser user does not inherit another user's draft state (`frontend/services/auth.ts`).
- On native, the same helper persists and restores the token with `expo-secure-store` (`frontend/services/auth.ts`).
- The shared API wrapper reads the token on every request and adds `Authorization: Bearer <token>` to all authenticated calls (`frontend/services/api.ts`).
- When any API call gets `401`, the frontend clears the stored token and dispatches `sacrifice-session-expired` so the UI can return to login instead of appearing signed in with a dead session (`frontend/services/api.ts`).

## Active gaps and limits
- There is no separate refresh-token artifact or multi-device session table; the current model is one mutable `auth_session_id` per user, so refresh/logout rotation revokes all older bearer tokens for that user at once (`backend/app/services/auth.py`, `backend/app/models/user.py`, `backend/app/routes/auth.py`).
- Email/password auth still lacks email verification, forgot-password/reset flows, and login/register rate limiting; the auth router calls those omissions out explicitly in comments (`backend/app/routes/auth.py`).
- On web, bearer tokens live in `localStorage`, so browser-side code execution or storage compromise is enough to reuse them until expiry or server-side rotation (`frontend/services/auth.ts`, `frontend/services/api.ts`).
