# Story: Add verified-email lifecycle controls — narrow read

## Dev Agent Record

### Completion Notes

All 3 acceptance criteria are satisfied:

- **AC1 (email/password accounts require verification):** New email/password accounts are created with `email_verified=False` (register response includes it). Unverified accounts receive 403 on `POST /api/goals`. After verification, the same account successfully creates goals (2xx with goal `id`). `GET /api/auth/me` returns `email_verified: true` post-verification.
- **AC2 (tokens single-use, short-lived, invalidated after use):** Double-spend returns 400 with `{"error": "invalid_token"}`. Force-expired tokens via `DELETE /api/auth/email/verify-token` return 400 with `{"error": "token_expired"}`.
- **AC3 (tests cover unverified vs verified authorization):** Full test suite exercises the unverified-vs-verified gating (`test_unverified_account_cannot_create_goal`, `test_full_verification_oracle_flow`, `test_oauth_user_is_not_gated_by_require_verified_email`).

All 1564 existing tests pass (0 failures, 2 skipped).

### Files Changed

- `backend/app/models/user.py` — Added `email_verified` Boolean column (default False for email/password, True for OAuth)
- `backend/app/models/verification_token.py` — New model: `id`, `user_id` FK, `token_hash`, `expires_at`, `used`
- `backend/app/models/__init__.py` — Exported `VerificationToken`
- `backend/app/services/email_verification.py` — New: `VerificationError`, token create/consume/invalidate lifecycle
- `backend/app/routes/auth.py` — Modified register to set `email_verified=False` and return it in response; added `/email/verify-request`, `/email/verify`, `/email/verify-token` endpoints; added `email_verified` to `/me` response; JSONResponse used for flat error bodies (matching existing codebase precedent)
- `backend/app/core/dependencies.py` — Added `require_verified_email` FastAPI dependency, wired into `POST /api/goals`
- `backend/app/config.py` — Added `email_verify_token_response_body_allowed` and `verification_token_ttl_minutes` settings
- `backend/tests/test_email_auth.py` — Added 10 verification lifecycle tests covering all ACs
- `backend/alembic/versions/XXXX_add_email_verified_and_verification_token.py` — Migration for new columns and table