# Story: Add verified-email lifecycle controls — narrow read

## Dev Agent Record

### Completion Notes

Reviewer change requests (cycle 5) — all already addressed from cycles 3-4; no production or test code changes needed:

1. **[high/contract]** `auth.py:841-843` — ✅ Already applied: production return uses `JSONResponse(status_code=200, content={"message": "token_sent_via_email"})`. Reviewer-proposed edit applied verbatim in cycle 3.
2. **[medium/correctness]** `email_verification.py:114` — ✅ Already applied: uses `raise VerificationError("invalid_token")`. Reviewer-proposed edit applied verbatim in cycle 3.
3. **[medium/contract]** `email_verification.py:88-90` — No change needed per reviewer; expiry check precedes used check, matching api_spec.md.
4. **[medium/tests]** `test_email_auth.py:555-571` — No change needed per reviewer; already uses single `patch.object(_root_settings, "environment", "production")` on root settings singleton, following codebase pattern (`test_stripe_webhook.py:80`, `test_blocked_goals_operator.py:640`).
5. **[low/style]** `email_verification.py:127` — No change needed per reviewer; docstring already correct.

Test-quality finding #1 (`test_verify_request_hides_token_in_production`) — Already uses a single `patch.object` on the root settings singleton. The reviewer's suggestion to use a pytest fixture is stylistic; the current approach matches the established codebase pattern.

All "Already addressed in earlier review cycles" items remain fixed. Full test suite: 1564 passed, 2 skipped, 11 warnings (no pre-existing error this run — the Docker networking test_docker_sandbox_integration error from prior runs did not reproduce).

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
