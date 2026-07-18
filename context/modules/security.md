# Security module

## Purpose
Documents credential rotation procedures, minimum-scope policies, and secret-handling conventions for Stripe, OAuth providers, and other integration credentials used by the Sacrifice backend.

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