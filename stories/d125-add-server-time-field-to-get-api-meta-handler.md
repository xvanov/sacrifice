# Story: D125 — Add `server_time` field to `GET /api/meta` handler

## Story

`GET /api/meta` currently returns static build facts (`service`, `version`). Add a `server_time` field — the server's current UTC time as a timezone-aware ISO-8601 string — to the existing handler. The endpoint remains unauthenticated and stateless. One-line handler addition in `backend/app/routes/meta.py`; extend `backend/tests/test_meta.py` to cover all three acceptance criteria.

## Acceptance Criteria

- [x] `GET /api/meta` (unauthenticated, as today) returns `200` and the body still contains `"service": "sacrifice"` and a non-empty string `"version"` — the existing contract is unchanged.
- [x] The body additionally contains `"server_time"`: a string parseable as an ISO-8601 timestamp with an explicit UTC offset (i.e. `datetime.fromisoformat(value)` succeeds and the parsed value is timezone-aware).
- [x] Two consecutive `GET /api/meta` calls both return `200` with a valid `server_time` (the field is computed per-request, not a crash-once static).

## Dev Agent Record

**Completion Notes:**
- Added `server_time` field to the handler return dict: `datetime.now(timezone.utc).isoformat()`.
- Existing test `test_meta_with_authorization_header_returns_same_contract` was renamed to `test_meta_missing_auth_returns_same_static_contract` and updated: it no longer asserts identical JSON (since `server_time` is per-request), instead asserts static fields match and both responses have valid `server_time`.
- Consolidated old AC1.2 (`has_service_field`) + AC1.3 (`service_is_sacrifice`) into `test_meta_service_is_sacrifice`; consolidated AC2.1–AC2.3 (`has_version_field`, `version_is_string`, `version_is_non_empty`) into `test_meta_version_is_non_empty_string` — matching the AC count in this story.
- All 10 tests pass green. `pytest --collect-only` collects 1571 tests with no errors.

**File List:**
- `backend/app/routes/meta.py`
- `backend/tests/test_meta.py`

## Senior Developer Review

<!-- to be filled by senior developer -->

## Review Follow-ups

<!-- to be filled if review produces follow-ups -->