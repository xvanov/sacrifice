# Security module

## What this module owns
The security module is the current set of protections around identity-bearing material, user-supplied secrets, and dangerous outbound requests. In the codebase today that spans bearer-token validation, OAuth state and auth-code replay defenses, password hashing, token-at-rest encryption for user-supplied GitHub credentials, SSRF guards for outbound verification fetches, and the dev-oriented CORS/auth conveniences that shape the remaining risk surface (`backend/app/core/dependencies.py`, `backend/app/routes/auth.py`, `backend/app/services/auth.py`, `backend/app/core/passwords.py`, `backend/app/core/crypto.py`, `backend/app/goal_types/github_repo/__init__.py`, `backend/app/workers/github_repo.py`, `backend/app/core/net_safety.py`, `backend/app/main.py`).

## Bearer-material threat model
A still-valid Sacrifice bearer token is enough to act as the user on the backend because the same `get_current_user(...)` dependency protects the routes that manage goals, proofs, dashboard data, uploads, notifications, chat sessions, Stripe setup intents, payment-method listings, payment-method deletion, payment history, and charity search (`backend/app/core/dependencies.py`, `backend/app/routes/goals.py`, `backend/app/routes/dashboard.py`, `backend/app/routes/notifications.py`, `backend/app/routes/uploads.py`, `backend/app/routes/chat.py`, `backend/app/routes/payment.py`).

That means compromise of bearer material is not just a profile-read issue. It directly enables account impersonation across the surfaces that can set up or remove saved payment methods, inspect payment history, search charities, create or alter goals, submit proof, upload media, and otherwise steer downstream pledge abuse while the token remains valid (`backend/app/routes/payment.py`, `backend/app/routes/goals.py`, `backend/app/routes/uploads.py`).

## Current replay and revocation defenses
### Session-bound access tokens
- Access tokens are JWTs with a per-token `jti` and a session-binding `sid` claim (`backend/app/services/auth.py`).
- The backend rejects any token whose `sid` no longer matches `users.auth_session_id` (`backend/app/core/dependencies.py`, `backend/app/models/user.py`).
- Every successful login, refresh, logout, and debug-token issuance rotates `users.auth_session_id`, which revokes all previously issued tokens for that user without a blacklist table (`backend/app/routes/auth.py`, `backend/app/services/auth.py`).
- Tests verify that an old token fails after refresh and after logout (`backend/tests/test_auth.py`).
- Access tokens are not single-use. Replay is blocked only after expiry or after some login, refresh, logout, or dev-token issuance rotates `users.auth_session_id`; until then, the same bearer token can be presented repeatedly, including to `/api/auth/refresh` itself (`backend/app/routes/auth.py`, `backend/app/services/auth.py`, `backend/app/core/dependencies.py`, `backend/tests/test_auth.py`).


### Single-use callback handoff
- OAuth callbacks do not redirect with the final app bearer token. They store `users.pending_auth_code_id`, mint a short-lived auth code carrying `purpose="auth_exchange"` and `code_id`, and redirect with that code instead (`backend/app/routes/auth.py`, `backend/app/services/auth.py`, `backend/app/models/user.py`).
- `/api/auth/exchange` only accepts the code while the stored `pending_auth_code_id` still matches, then rotates the session and clears the code slot. Replay of the same code fails (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- Browser callbacks also require the `oauth_state` cookie to match the returned state. CLI/mobile flows encode the nonce into the state value itself, and mobile redirects are allowlisted by scheme/origin (`backend/app/routes/auth.py`).

### Password handling
- Email/password auth uses `passlib` with bcrypt behind the small wrapper in `backend/app/core/passwords.py` (`backend/app/core/passwords.py`, `backend/pyproject.toml`).
- Password verification treats missing or malformed hashes as non-matches, preventing malformed stored values from becoming accidental logins (`backend/app/core/passwords.py`).

## Sensitive secrets at rest
### User-supplied GitHub tokens
- The `github_repo` goal type encrypts a supplied `github_token` before writing it into proof/criteria payloads by calling `encrypt_token(...)` (`backend/app/goal_types/github_repo/__init__.py`).
- The GitHub verification worker decrypts that stored value right before adding `Authorization: Bearer ...` to outbound GitHub API requests (`backend/app/workers/github_repo.py`).
- `encrypt_token(...)` stores ciphertext as `fernet:<ciphertext>`, while `decrypt_token(...)` still accepts old plaintext rows for backward compatibility (`backend/app/core/crypto.py`).
- The Fernet key comes from `settings.token_encryption_key`; if unset, it is deterministically derived from `settings.jwt_secret` so development environments still work (`backend/app/core/crypto.py`, `backend/app/config.py`).

### Operational implication
At-rest encryption reduces the blast radius of a raw database read for newly encrypted GitHub personal access tokens, but the fallback to legacy plaintext means older rows may still be directly readable until rewritten, and the default dev behavior couples encryption strength to the JWT secret if a dedicated encryption key is not configured (`backend/app/core/crypto.py`, `backend/app/config.py`).

## Outbound-request safety
- `assert_public_url(...)` rejects non-HTTP(S) URLs and any host that is or resolves to loopback, RFC1918/private, link-local, reserved, multicast, or unspecified IP space (`backend/app/core/net_safety.py`).
- The file-level docstring calls out the reason: goal verification can otherwise become SSRF plus exfiltration against cloud metadata endpoints, localhost services, or other internal addresses (`backend/app/core/net_safety.py`).
- The same helper explicitly documents a residual DNS rebinding gap unless callers pin the resolved IP into the transport and disable redirects (`backend/app/core/net_safety.py`).

## Security-relevant product behaviors and tradeoffs
- `POST /api/auth/email/login` returns `409 {error:"account_exists", provider:"..."}` when the email belongs to another provider. The route comment explicitly accepts that this leaks registration status for UX reasons (`backend/app/routes/auth.py`).
- `get_or_create_user(...)` refuses unverified same-email cross-provider sign-in attempts, which closes the email-claim takeover case that the auth tests cover (`backend/app/services/auth.py`, `backend/tests/test_auth.py`).
- The frontend web app persists the bearer token in `localStorage`, while native uses `expo-secure-store`; the shared API wrapper clears the token after a 401 but does not otherwise constrain browser-side reuse (`frontend/services/auth.ts`, `frontend/services/api.ts`).
- The backend's CORS middleware allows credentials and is intentionally permissive for a list of localhost/LAN dev origins plus an ngrok hostname, matching the current local-development model rather than a locked-down production allowlist (`backend/app/main.py`).

## Current gaps to keep in mind
- There is no separate refresh-token family, device/session inventory, or selective token revocation. Rotation is coarse-grained at the user row's `auth_session_id` (`backend/app/services/auth.py`, `backend/app/models/user.py`, `backend/app/routes/auth.py`).
- Email/password auth still lacks email verification, password reset, and rate limiting, all called out in the auth router comments (`backend/app/routes/auth.py`).
- The frontend callback helpers still accept `access_token` in redirect URLs for backwards compatibility, so server-side OAuth flows must keep using short-lived auth codes rather than returning the final bearer token in the URL (`frontend/services/auth.ts`, `backend/app/routes/auth.py`).

- A web-origin compromise that can read `localStorage` can immediately impersonate the account against payment and goal endpoints until server-side rotation or expiry cuts the token off (`frontend/services/auth.ts`, `frontend/services/api.ts`, `backend/app/routes/payment.py`, `backend/app/routes/goals.py`).
- If `token_encryption_key` is left empty, the same `jwt_secret` underpins both JWT signing and derived-at-rest encryption, which is convenient for development but couples two security concerns into one secret (`backend/app/core/crypto.py`, `backend/app/config.py`).
