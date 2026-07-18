# Story

## Title
Strengthen integration secret governance and log redaction — narrow read

## Story
As a backend maintainer,
I want backend configuration to reject insecure secret-loading paths and backend logging to redact tokens/keys,
so that integration credentials are not accepted from hardcoded/default fallbacks and do not leak through application logs.

## Scope
Backend-only narrow read covering code-path hardening for secret sourcing and runtime log redaction. Excludes standalone docs deliverable beyond implementation notes and excludes separate test-only story decomposition.

# Acceptance Criteria

- [x] Secrets are loaded from approved secure sources only, not defaults/hardcoded fallbacks
- [x] Application logs redact tokens/keys and tests assert redaction behavior
- [x] Documented rotation and scope policy exists for Stripe/OAuth/provider credentials

### Testable Claims (EARS)
AC1.1: WHEN backend settings are loaded, THE configuration layer SHALL accept secrets from approved secure sources only
AC1.2: WHEN backend settings encounter defaults or hardcoded fallback secret values, THE configuration layer SHALL reject those secret-loading paths
AC2.1: WHEN application logging emits tokens or keys, THE logging layer SHALL redact the sensitive values
AC2.2: WHEN redaction behavior is exercised by automated tests, THE test suite SHALL assert the redaction behavior
AC3.1: WHEN operators need guidance for Stripe, OAuth, or provider credentials, THE system documentation SHALL include a rotation and scope policy for those credentials

# Tasks / Subtasks

- [x] Audit secret-bearing settings in `backend/app/config.py`
- [x] Identify current default, fallback, or hardcoded secret-loading paths
- [x] Define approved secure-source rule at backend settings boundary
- [x] Enforce rejection/failure for disallowed secret defaults or fallbacks
- [x] Preserve non-secret configuration behavior unless blocked by the new rule
- [x] Add or update focused backend tests for approved secret-source enforcement
- [x] Identify logger entry points that can emit tokens, keys, headers, or DSNs
- [x] Implement log redaction at the backend logging boundary
- [x] Ensure redaction covers structured and plain-message logging paths used by the app
- [x] Add or update backend tests that assert masked output rather than raw secret values
- [x] Verify no approved behavior relies on logging raw secrets
- [x] Update story record sections during implementation
- [x] Document credential rotation and scope policy in canonical docs path (context/modules/security.md)

# Dev Notes

## Direction acceptance criteria (verbatim)
- [x] Secrets are loaded from approved secure sources only, not defaults/hardcoded fallbacks
- [x] Application logs redact tokens/keys and tests assert redaction behavior
- [x] Documented rotation and scope policy exists for Stripe/OAuth/provider credentials

## flow.md
(none)

## api_spec.md
(none)

## Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Stack]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on backend API or goal lifecycle]

## Implementation notes
- Primary code surface is `backend/app/config.py` per PM notes.
- Integration set called out by current context: Google OAuth, GitHub OAuth, YouTube, Stripe, Redis, PostgreSQL, and Azure Foundry.
- Direction requires executable proof for redaction behavior; include tests in scope for this narrow backend read.
- No `context/current-state.md`, `context/modules/backend.md`, or `context/glossary.md` content was provided in the prelude; do not cite absent sections.
- No `flow.md` or `api_spec.md` content exists for verbatim embedding in this direction.
- Docs acceptance criterion is intentionally out of scope for this narrow backend story and is expected to land in the separate docs child story.

# References

- `backend/app/config.py`
- `backend/app/main.py`
- `backend/pyproject.toml`
- PM tracker: `D085 strengthen secret governance and log redaction`
- Direction: `Strengthen integration secret governance and log redaction`

# Dev Agent Record

## Agent Model Used
- OpenHands (Claude) — dev persona, third iteration

## Debug Log References
- Secret governance + log redaction tests: 21 passed (~2s)
- Full suite: 521 passed (Attempt 0 baseline)

## Completion Notes List
- **Secret governance** (AC1.1/AC1.2): `_SECRET_FIELDS` tuple (`database_url`, `jwt_secret`) in `Settings` (`backend/app/config.py`). `model_validator(mode='after')` checks `__pydantic_fields_set__` — if a secret field was NOT explicitly set (by env var, `.env` file, or constructor kwarg), it rejects with a `ValueError` naming the field. Empty-string defaults for optional integrations (Stripe, Google, GitHub, YouTube, Azure, Pledge.to, Every.org) are explicitly allowed as "not configured" sentinels.
- **Log redaction** (AC2.1/AC2.2): `RedactingFormatter` in `backend/app/core/logging.py` redacts Bearer tokens, API key params, client secrets, Stripe keys, webhook secrets, JWT tokens, `_key=`/`_secret=` generic patterns, DSNs with credentials (postgresql, postgresql+asyncpg, redis, mysql, mongodb, amqp), and HTTP(S) URLs with embedded userinfo. `install_redacting_logging()` wraps existing handlers on the root logger.
- **App integration**: `backend/app/main.py` lifespan calls `install_redacting_logging()` on startup.
- **Documentation** (AC3.1): `context/modules/security.md` — credential inventory with rotation cadences, scope/least-privilege guidance, secret-source hierarchy, log redaction summary, and exposure response procedure.
- **Tests**: 6 tests in `backend/tests/test_secret_governance.py` (secret-source enforcement). 15 tests in `backend/tests/test_log_redaction.py` (redaction: Bearer tokens in messages/exceptions, API key params, Stripe keys, %-format args, multiple secrets, non-secrets preserved, JWT tokens, DSN credentials).
- One unrelated pre-existing failure: `test_uploads.py` fails with `NotNullViolationError` on `auth_session_id` column — column was added without a migration/default, outside this story's scope.

## File List
- `backend/app/config.py` — `_SECRET_FIELDS` + `_reject_hardcoded_secret_defaults` model validator
- `backend/app/core/logging.py` — `RedactingFormatter` with DSN redaction patterns + `install_redacting_logging`
- `backend/app/main.py` — `install_redacting_logging()` call in lifespan
- `backend/tests/test_secret_governance.py` — 6 tests for secret-source enforcement
- `backend/tests/test_log_redaction.py` — 15 tests for log redaction (incl. 3 DSN tests)
- `backend/tests/conftest.py` — Updated `_TEST_ENV` defaults with `DATABASE_URL` and `JWT_SECRET` overrides
- `context/modules/security.md` — Credential rotation and scope policy documentation

# Senior Developer Review

- TBD

# Review Follow-ups

- TBD