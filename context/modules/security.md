# Security module

## Purpose
The security module is the cross-cutting set of controls around bearer handling, replay resistance, provider-conflict protection, encryption of sensitive tokens at rest, and secret-handling/log-redaction conventions for Stripe, OAuth providers, and other integration credentials used by the Sacrifice backend (`backend/app/core/dependencies.py`, `backend/app/core/crypto.py`, `backend/app/core/logging.py`, `backend/tests/test_auth.py`, `backend/tests/test_email_auth.py`).

## Current controls
- OAuth browser and native flows use state validation. The backend stores a raw `oauth_state` cookie, verifies the returned state, and rejects callbacks when the cookie is missing or mismatched (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- OAuth callbacks produce one-time auth codes instead of raw bearer redirects. Tests assert that exchanging a code works once and a replay receives `401` (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- Cross-provider email takeover is blocked. Tests cover the `409 account_exists` behavior when an attacker attempts to sign in with a different provider that claims an already-owned email without verified ownership (`backend/tests/test_auth.py`, `backend/tests/test_email_auth.py`).
- Sensitive third-party tokens persisted by the backend are encrypted with Fernet and tagged with a `fernet:` prefix so old plaintext rows remain readable during migration (`backend/app/core/crypto.py`, `backend/tests/test_crypto.py`).

## Security-critical attack surface
The same bearer token unlocks authenticated reads and writes across goals, payments, notifications, uploads, and dashboard flows because those routes all trust the shared current-user dependency (`backend/app/core/dependencies.py`, `backend/app/routes/goals.py`, `backend/app/routes/payment.py`). In this product, compromise of bearer material is therefore equivalent to account impersonation and can lead directly to downstream pledge abuse.

## Remaining gaps visible today
- The CLI stores bearer tokens in plaintext config under the user's home directory (`backend/cli/client.py`).
- **Email/password accounts have no mailbox proof.** `POST /api/auth/email/register`
  issues a session without any verification step: there is no verification-token
  issuance or consumption, and no gate that withholds sensitive operations from an
  unverified account. `email_verified` exists ONLY as a value read back from an
  OAuth provider (`backend/app/services/auth.py`), so it says nothing about
  email/password signups. This is the one real gap in this area
  (`backend/app/routes/auth.py`, `backend/tests/test_email_auth.py`).
  <!-- CORRECTED 2026-08-08. This bullet previously read "no visible rate
  limiting, password reset, or email-verification enforcement". Two of those
  three were already SHIPPED, and the stale claim had a cost: the scheduled
  `security` persona reads this file, so it re-filed password reset as factory
  directions d094, d098, d108, d113 and 117/118 — five times, once AFTER it
  shipped. Verify against the route table before re-adding a "missing" claim
  here. -->
- Rate limiting IS enforced on the auth surface: `check_auth_rate_limit`
  (`backend/app/core/rate_limiter.py`) is a dependency on every route in
  `backend/app/routes/auth.py`, including `/email/register`, `/email/login` and
  `/password/reset/request`.
- Password reset IS implemented and is not a gap: `POST /api/auth/password/reset/request`
  and `POST /api/auth/password/reset/confirm`, with single-use token binding via
  `backend/app/models/reset_token_jti.py` and post-reset session revocation
  (shipped by factory direction 113 / story 138).
- Database token encryption falls back to a key derived from `jwt_secret` when `token_encryption_key` is unset, which is convenient for development but couples two secrets (`backend/app/core/crypto.py`, `backend/tests/test_crypto.py`).

## Credential inventory

| Credential | Config field | Scope / least-privilege guidance | Rotation cadence |
| --- | --- | --- | --- |
| Stripe secret key | `STRIPE_SECRET_KEY` | Use a restricted key scoped to the specific Stripe account. Never use a platform-level key when a Connect-account key suffices. Keep the key server-side only — `stripe.api_key` is set exclusively in backend processes. | Rotate every 90 days or immediately on suspected exposure. |
| Stripe publishable key | `STRIPE_PUBLISHABLE_KEY` | Client-safe. Only exposed through the `/api/payment/config` endpoint for the frontend to initialize Stripe.js. | Rotate alongside the secret key. |
| Stripe webhook secret | `STRIPE_WEBHOOK_SECRET` | Used to verify webhook signatures. Must be unique per Stripe environment (test/live). | Rotate every 90 days or when the Stripe account credentials change. |
| Google OAuth client ID / secret | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Use a dedicated OAuth 2.0 client restricted to the specific redirect URIs configured in `GOOGLE_REDIRECT_URI`. Do not reuse a general-purpose project client. The secret must never reach the frontend — OAuth code exchange happens server-side. | Rotate every 12 months or on suspected exposure. |
| GitHub OAuth client ID / secret | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | Use a dedicated OAuth App (not a personal access token) with `user:email` scope only. The secret is used server-side during code exchange. | Rotate every 12 months or on suspected exposure. |
| YouTube API key | `YOUTUBE_API_KEY` | Use an API key restricted to the YouTube Data API v3. Bind the key to the backend's source IP or HTTP referrer where possible. | Rotate every 12 months or on suspected exposure. |
| Every.org API keys | `EVERY_ORG_API_KEY`, `EVERY_ORG_API_SECRET` | Public key (`pk_*`) authenticates search requests; private key (`sk_*`) is for privileged endpoints and must never reach the client. | Rotate every 12 months or on suspected exposure. |
| Pledge.to API key | `PLEDGE_API_KEY` | Server-side only; used for donation creation. Never expose to the client. | Rotate every 12 months or on suspected exposure. |
| Azure Foundry API key | `AZURE_FOUNDRY_API_KEY` | Use a key scoped to the specific model deployment (`AZURE_FOUNDRY_DEPLOYMENT`). Bind to backend source IP if supported. | Rotate every 12 months or on suspected exposure. |
| Database URL | `DATABASE_URL` | Use a dedicated PostgreSQL role with only the permissions the backend needs: DML on public tables (SELECT, INSERT, UPDATE, DELETE) plus CREATE TABLE for Alembic migrations during deploy. Never use a superuser account for the application connection. | Rotate every 6 months or on personnel change. |
| JWT secret | `JWT_SECRET` | Used to sign access tokens. Must be a high-entropy random string (≥256 bits / 32 bytes). | Rotate every 6 months or immediately on suspected exposure. A rotation invalidates all existing access tokens. |
| Token encryption key | `TOKEN_ENCRYPTION_KEY` | 32-byte url-safe base64 Fernet key for encrypting user-supplied tokens at rest (e.g., GitHub PATs). If unset, a key is derived from `JWT_SECRET`. For production, provide an independent key. | Rotate alongside JWT secret. Note: rotation requires re-encrypting stored tokens. |

## Secret-source hierarchy

Secrets are loaded in this order of precedence (highest wins):

1. Explicit constructor kwargs (`Settings(database_url=...)`) — for testing only.
2. Environment variables (`DATABASE_URL`, `JWT_SECRET`, etc.).
3. `.env` file (relative to `backend/`).

Hardcoded defaults are **rejected at startup** for the following fields:
`DATABASE_URL`, `JWT_SECRET`. The application will fail fast with a `ValueError`
naming the offending field rather than silently using an insecure default.
This enforcement is implemented in `app.config.Settings._reject_hardcoded_secret_defaults`.

## Log redaction

All application logs pass through `RedactingFormatter` (`app/core/logging.py`),
which strips the following patterns before emission:

- Bearer tokens (`Authorization: Bearer <token>`)
- API keys in query parameters (`apiKey=`, `key=`)
- Stripe secret keys (`sk_live_*`, `sk_test_*`) and webhook secrets (`whsec_*`)
- JWT tokens (three-segment `eyJ...` patterns)
- Database and broker DSNs with embedded credentials
- HTTP(S) URLs with embedded userinfo

Redaction is installed at application startup via `install_redacting_logging()`
called from the FastAPI lifespan, so even logs emitted before uvicorn takes
over are covered.

## Exposure response

If a credential is suspected of being exposed:

1. **Rotate immediately**: Generate a new credential at the provider; update the
   corresponding environment variable; restart the backend.
2. **Revoke the old credential**: Delete or deactivate it at the provider.
3. **Audit access logs**: Check provider audit trails for unauthorized use
   during the exposure window.
4. **Rotate JWT secret**: If the exposure was in a running process (memory dump,
   log file), rotate `JWT_SECRET` to invalidate all existing sessions.
5. **Check Stripe**: For Stripe key exposure, review the Stripe dashboard for
   unrecognized charges, customers, or account changes.
