# Story: Add verified-email lifecycle controls — narrow read

## Dev Agent Record

### Completion Notes

Reviewer change requests (cycle 4) — all already addressed from cycle 3:

1. **[high/contract]** `auth.py:841-843` — ✅ Already applied: production return uses `JSONResponse(status_code=200, content={"message": "token_sent_via_email"})`.
2. **[medium/correctness]** `email_verification.py:114` — ✅ Already applied: uses `raise VerificationError("invalid_token")`.
3. **[medium/contract]** `email_verification.py:88-90` — No change needed; expiry check precedes used check.
4. **[medium/tests]** `test_email_auth.py:555-571` — Already fixed in cycle 2: single `patch.object(_root_settings, "environment", "production")` on root singleton. Follows codebase pattern used in `test_stripe_webhook.py:80,98,119,133` and `test_blocked_goals_operator.py:640,659`.
5. **[low/style]** `email_verification.py:127` — No change needed; docstring already reads ``False`` otherwise.

All previously-addressed items from cycles 1-2 remain fixed. Full test suite: 1563 passed, 2 skipped, 11 warnings, 1 pre-existing error in `test_dev_sandbox_integration.py` (Docker networking, unrelated).

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
