# Story

## Title
Enforce email verification before sensitive account usage — narrow read

## Slug
`enforce-email-verification-before-sensitive-account-usage-na`

## Scope
`backend`

# Dev Agent Record

## Agent Model Used
- openhands

## Debug Log References
- N/A

## Completion Notes
- All acceptance criteria satisfied (AC1.1, AC2.1–AC2.5, AC3.1–AC3.2).
- Email verification tokens: cryptographically signed (JWT via jose), expiring (24h default), single-use (JTI consumed on redemption).
- One sensitive path wired: POST /api/goals requires verified email via `require_verified_email` dependency.
- New email/password accounts default to `email_verified=False`; OAuth accounts default to `email_verified=True` (unchanged behavior).
- Reviewer change requests (cycle 3):
  1. **[high/correctness]** JTI consumption (`email_verification_jti = None`) on successful verification was already applied at auth.py:629 from a prior attempt. Reviewer FIND string (lacking the null line) does not match current code; edit already applied.
  2. **[medium/tests]** Expired-token test was already restructured to use `patch("app.services.auth.EMAIL_VERIFY_EXPIRE_HOURS", -1)` during issuance — isolates expiry rejection without mixing behaviors. Fresh-token happy path extracted to separate test. Reviewer FIND string (old mixed-behavior test) does not match current code; edit already applied.
- All 747 backend tests pass (1 pre-existing skip). 6 e2e_test.py failures are pre-existing CLI auth issues, unrelated to this story.
- No regression: OAuth accounts, existing auth flows, and all other routes unaffected.

## File List
- backend/app/routes/auth.py (JTI null on success)
- backend/app/routes/goals.py (require_verified_email on POST /api/goals)
- backend/app/core/dependencies.py (require_verified_email dependency)
- backend/app/models/user.py (email_verified, email_verification_jti columns)
- backend/app/services/auth.py (EMAIL_VERIFY_PURPOSE, EMAIL_VERIFY_EXPIRE_HOURS, _create_signed_token, decode_email_verification_token)
- backend/tests/test_email_verification.py (9 tests: happy path, signed/expiring, 409 on re-request, fresh redeem, expired rejection, single-use rejection, unverified 403, verified 201, OAuth unaffected)
- backend/alembic/versions/ffabb0a9d9d1_add_email_verification_columns.py (migration)

# Senior Developer Review

- TBD

# Review Follow-ups

- TBD