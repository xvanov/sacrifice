# Story: Add verified-email lifecycle controls — narrow read

## Dev Agent Record

### Completion Notes

Reviewer change requests applied (post-approval fixes):

1. **[medium/contract]** `auth.py:841-843` — Production return changed from `{"verification_token": None}` to `{}` — token field is entirely absent in production, not null-valued.
2. **[medium/correctness]** `email_verification.py:105-113` — Already-verified user anomaly now raises `"already_verified"` error code (distinct from `"invalid_token"`) with a logger warning, making the discrepancy observable during development.
3. **[medium/contract]** `email_verification.py:88` — Added comment documenting that expiry check precedes used check so expired tokens always surface as `token_expired`.
4. **[medium/tests]** `test_email_auth.py:555-571` — `test_verify_request_hides_token_in_production` now patches both `app.routes.auth.settings` AND `app.services.email_verification.settings` (with `verification_token_ttl_minutes=30`), so the production guard in both modules is exercised. Assertion changed to `"verification_token" not in body` to match the new production return shape.
5. **[low/style]** `email_verification.py:127` — Fixed misleading docstring: `invalidate_tokens_for_user` now says "``False`` otherwise" instead of "``False`` if no outstanding tokens existed".

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