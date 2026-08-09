# Story: D117 Add verified-email lifecycle controls — broad read

**Scope:** backend | **Chain:** tdd | **Points:** 13

## Acceptance Criteria

- [x] New email/password accounts require successful verification before sensitive operations
- [x] Verification tokens are single-use, short-lived, and invalidated after use
- [x] Tests cover unverified vs verified authorization behavior

## Dev Agent Record

### Completion Notes

All 11 task groups completed. All acceptance criteria satisfied with passing tests.

- **Task 1 (User model):** `User.email_verified` column added (Boolean, default=False, nullable=False). Migration `074067e27170`.
- **Task 2 (VerificationToken model):** Created with fields: id (UUID PK), user_id (FK to users), token_hash (String, indexed), expires_at (DateTime), used (Boolean, default False), created_at (DateTime). Same migration as Task 1.
- **Task 3 (Register):** Email registration sets `email_verified=False`. OAuth sets `email_verified=True`. Response schema includes `email_verified`.
- **Task 4 (GET /me):** Response includes `email_verified` field from current user record.
- **Task 5 (Verify-request):** Generates cryptographically-random token (`secrets.token_urlsafe(32)`), stores SHA-256 hash in VerificationToken with 15-min expiry. Returns plaintext token in response body as observable substitute (logs a warning if `config.ENVIRONMENT == "production"` since the token should be emailed instead in production). Rejects already-verified accounts with 409. Rate-limited with per-user 60s cooldown via `has_outstanding_verification_token`.
- **Task 6 (Verify):** Token consumption via POST `/api/auth/email/verify`. Single DB transaction marks user verified + token used. `token_expired` for expired tokens, `invalid_token` for used/unknown tokens (indistinguishable).
- **Task 7 (Force-expire):** DELETE `/api/auth/email/verify-token` invalidates outstanding token for authenticated user only. Returns 404 if no token exists. Cross-user isolation enforced.
- **Task 8 (Gating):** `require_verified_email` FastAPI dependency raises 403 for unverified users. Wired into `POST /api/goals`. OAuth users (email_verified=True) pass through.
- **Task 9 (Rate limiting):** Added rate limiting on register, login, and verify-request endpoints using in-memory sliding-window rate limiter.
- **Task 10 (Cleanup):** CLI command `sacrifice cleanup-verification-tokens` removes VerificationToken rows older than 24 hours.
- **Task 11 (Oracle flows):** Full E2E tests for all AC flows: register→403→verify→login→2xx, single-use double-spend, force-expire reuse, OAuth bypass.

### File List

- `backend/app/config.py` — Added `ENVIRONMENT` setting
- `backend/app/core/dependencies.py` — Added `require_verified_email`, `check_register_rate_limit`, `check_login_rate_limit`, `check_verify_request_rate_limit`, `check_verify_cooldown`
- `backend/app/core/rate_limiter.py` — In-memory sliding-window rate limiter
- `backend/app/models/__init__.py` — Export `VerificationToken`
- `backend/app/models/user.py` — Added `email_verified` field, `VerificationToken` model
- `backend/app/routes/auth.py` — New/updated endpoints: verify-request, verify, verify-token, updated register, me
- `backend/app/routes/goals.py` — Wired `require_verified_email` into POST /api/goals
- `backend/app/services/auth.py` — Token creation, hashing, verification, cleanup helpers
- `backend/alembic/versions/074067e27170_add_email_verified_and_verification_.py` — Migration
- `backend/cli/main.py` — Added `cleanup-verification-tokens` command
- `backend/tests/conftest.py` — Added `_clear_rate_limit_store` autouse fixture
- `backend/tests/test_auth.py` — Updated register/me tests for email_verified field
- `backend/tests/test_auth_rate_limit.py` — Rate limiting tests including verify-request (9.4)
- `backend/tests/test_deadline_worker.py` — Updated INSERT to include email_verified column
- `backend/tests/test_e2e_matrix.py` — Updated INSERT to include email_verified column
- `backend/tests/test_email_auth.py` — Oracle flow tests, token lifecycle tests, cleanup test
- `backend/tests/test_payments_ledger_constraint.py` — Updated INSERT to include email_verified column
- `backend/tests/test_proof_dispatch_reconcile.py` — Updated INSERT to include email_verified column