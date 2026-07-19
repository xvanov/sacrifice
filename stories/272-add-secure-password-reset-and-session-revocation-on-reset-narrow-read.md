# Story

## Title
Add secure password reset and session revocation on reset — narrow read

## Dev Agent Record

### Agent Model Used
- OpenHands agent (GPT-5)

### Debug Log References
- `pytest tests/test_auth.py tests/test_email_auth.py` ✅ (55 passed)
- `pytest tests` ❌ (persistent baseline failures: `tests/test_chat_sessions_api.py::test_chat_sessions_migration_creates_required_columns_and_types`, `tests/test_media_uploads.py::TestMediaUploadMigration::test_model_persist_orphan`, `tests/test_media_uploads.py::TestMediaUploadMigration::test_model_persist_goal_linked`; failures are unchanged and still rooted in alembic `upgrade head` with multiple migration heads)

### Completion Notes List
- `POST /api/auth/reset-password` now validates `new_password` through the same shared email-auth policy path used by registration (`validate_email_auth_password`), returning HTTP 400 with policy detail when violated.
- Shared email-auth password policy primitives live in `app/services/auth.py` (`validate_email_auth_password`, `PasswordPolicyError`) and are enforced by both `/email/register` and `/reset-password`.
- `test_reset_token_throttling_after_max_attempts` now drives lockout via real repeated failed `/api/auth/reset-password` calls (no direct mutation of `attempts`) and verifies the persisted attempt counter reaches `MAX_RESET_ATTEMPTS`.
- `test_reset_token_lifecycle_created_expires_consumed` uses `pytest.raises(ResetTokenError)` for consumed-token rejection.
- `test_reset_token_has_expiry` now asserts expiry metadata bounds and explicitly forces token expiration to verify validation failure.
- Full backend suite remains blocked by the pre-existing migration-head failures outside this auth/reset story scope.

### File List
- `backend/app/routes/auth.py`
- `backend/app/services/auth.py`
- `backend/tests/test_auth.py`
- `backend/tests/test_email_auth.py`
- `stories/272-add-secure-password-reset-and-session-revocation-on-reset-narrow-read.md`
