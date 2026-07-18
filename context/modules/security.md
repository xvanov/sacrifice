# Security module

## Purpose
The security module is the cross-cutting set of controls around bearer handling, replay resistance, provider-conflict protection, and encryption of sensitive tokens at rest (`backend/app/core/dependencies.py`, `backend/app/core/crypto.py`, `backend/tests/test_auth.py`, `backend/tests/test_email_auth.py`).

## Current controls
- OAuth browser and native flows use state validation. The backend stores a raw `oauth_state` cookie, verifies the returned state, and rejects callbacks when the cookie is missing or mismatched (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- OAuth callbacks produce one-time auth codes instead of raw bearer redirects. Tests assert that exchanging a code works once and a replay receives `401` (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- Cross-provider email takeover is blocked. Tests cover the `409 account_exists` behavior when an attacker attempts to sign in with a different provider that claims an already-owned email without verified ownership (`backend/tests/test_auth.py`, `backend/tests/test_email_auth.py`).
- Sensitive third-party tokens persisted by the backend are encrypted with Fernet and tagged with a `fernet:` prefix so old plaintext rows remain readable during migration (`backend/app/core/crypto.py`, `backend/tests/test_crypto.py`).

## Security-critical attack surface
The same bearer token unlocks authenticated reads and writes across goals, payments, notifications, uploads, and dashboard flows because those routes all trust the shared current-user dependency (`backend/app/core/dependencies.py`, `backend/app/routes/goals.py`, `backend/app/routes/payment.py`). In this product, compromise of bearer material is therefore equivalent to account impersonation and can lead directly to downstream pledge abuse.

## Remaining gaps visible today
- The CLI stores bearer tokens in plaintext config under the user's home directory (`backend/cli/client.py`).
- The inspected email auth surface shows no visible rate limiting, password reset, or email-verification enforcement (`backend/app/routes/auth.py`, `backend/tests/test_email_auth.py`).
- Database token encryption falls back to a key derived from `jwt_secret` when `token_encryption_key` is unset, which is convenient for development but couples two secrets (`backend/app/core/crypto.py`, `backend/tests/test_crypto.py`).
