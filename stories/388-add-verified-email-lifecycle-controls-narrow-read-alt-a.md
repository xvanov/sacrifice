# Story: Add verified-email lifecycle controls — narrow read

## Dev Agent Record

### Completion Notes

Reviewer change requests applied (cycle 3):

1. **[high/contract]** `auth.py:841-843` — Production return changed from `{}` to `JSONResponse(status_code=200, content={"message": "token_sent_via_email"})` per reviewer-proposed edit. The api_spec requires `{"verification_token": "string"}` for 200; in production the token is sent via email only so a descriptive message is returned instead.
2. **[medium/correctness]** `email_verification.py:114` — Changed `VerificationError("already_verified")` to `VerificationError("invalid_token")` per reviewer-proposed edit. The api_spec defines only `token_expired` and `invalid_token` for POST /api/auth/email/verify; `already_verified` is not a valid error code for this endpoint.
3. **[medium/contract]** `email_verification.py:88-90` — No change needed; expiry check already precedes used check, matching spec requirement.
4. **[medium/tests]** `test_email_auth.py:555-571` — `test_verify_request_hides_token_in_production` simplified to use `patch.object(settings, "environment", "production")` on the root config singleton instead of patching two separate module-level imports. All consumers share the same settings object, so one patch covers both modules.
5. **[low/style]** `email_verification.py:127` — No change needed; docstring already reads ``False`` otherwise.

All 1550 tests pass (2 skipped, 11 warnings). The 1 error in `test_dev_sandbox_integration.py` is a pre-existing Docker networking issue unrelated to these changes.

### Files Changed

- `backend/app/models/user.py` — Added `email_verified` Boolean column (default False for email/password, True for OAuth)
- `backend/app/models/verification_token.py` — New model: `id`, `user_id` FK, `token_hash`, `expires_at`, `used`
- `backend/app/models/__init__.py` — Exported `VerificationToken`
- `backend/app/services/email_verification.py` — New: `VerificationError`, token create/consume/invalidate lifecycle
- `backend/app/routes/auth.py` — Modified register to set `email_verified=False` and return it in response; added `/email/verify-request`, `/email/verify`, `/email/verify-token` endpoints; added `email_verified` to `/me` response; JSONResponse used for flat error bodies (matching existing codebase precedent at `auth.py:589`)
- `backend/app/core/dependencies.py` — Added `require_verified_email` FastAPI dependency, wired into `POST /api/goals`
- `backend/app/config.py` — Added `email_verify_token_response_body_allowed` and `verification_token_ttl_minutes` settings
- `backend/tests/test_email_auth.py` — Added 10 verification lifecycle tests covering all ACs
- `backend/alembic/versions/XXXX_add_email_verified_and_verification_token.py` — Migration for new columns and table
