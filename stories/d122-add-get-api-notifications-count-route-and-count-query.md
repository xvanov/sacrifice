# Story: D122: Add GET /api/notifications/count route and count query

## Story

As an authenticated user, I want a single endpoint that returns my total notification count (all types, read and unread) so that I can display "47 notifications" without paging the full list client-side.

Add `GET /api/notifications/count` to the existing notifications router. Reuse `get_current_user` (already imported in the router's neighborhood). Follow the pattern of `get_unread_count` in `backend/app/services/notification.py:82` — one query, one route, one new test file.

## Acceptance Criteria

- [x] Immediately after registering a new account (no goals, no notifications yet created for that account), `GET /api/notifications/count` returns `200` with body `{"count": 0}` for that caller.
- [x] After that same caller creates exactly one goal via `POST /api/goals`, `GET /api/notifications/count` for that caller returns `200` with body `{"count": 1}` — reflecting the single automatic `goal_created` notification that goal creation already fires (`backend/app/routes/goals.py:139-146`, verified: this is existing behavior, not new in this direction).
- [x] An unauthenticated `GET /api/notifications/count` is rejected rather than returning any count (assert status `401` only — see the operator-ratified rule below on error-path bodies this direction did not introduce).

### Testable Claims (EARS)

AC1.1: WHEN a newly registered caller (zero goals, zero notifications) sends `GET /api/notifications/count` with their valid bearer token, THE system SHALL return HTTP 200 with body `{"count": 0}`.
AC2.1: WHEN the same caller creates exactly one goal via `POST /api/goals` (which fires one `goal_created` notification as existing behavior), THEN sends `GET /api/notifications/count`, THE system SHALL return HTTP 200 with body `{"count": 1}`.
AC3.1: WHEN an unauthenticated request (no `Authorization` header) is sent to `GET /api/notifications/count`, THE system SHALL return HTTP 401.
AC3.2: WHEN a request with an expired token is sent to `GET /api/notifications/count`, THE system SHALL return HTTP 401.
AC3.3: WHEN a request with a malformed token is sent to `GET /api/notifications/count`, THE system SHALL return HTTP 401.

## Tasks/Subtasks

### 1. Add `get_total_count` to notification service
- [x] Add `async def get_total_count(db: AsyncSession, user_id: int) -> int` to `backend/app/services/notification.py`
- [x] Query: `SELECT COUNT(*) FROM notifications WHERE user_id = <user_id>` (no type/read filters)
- [x] Follow pattern of `get_unread_count` (line 82): same file, same param shape, same return type

### 2. Add route to notifications router
- [x] Add `GET /api/notifications/count` to `backend/app/routes/notifications.py`
- [x] Dependency: `current_user: User = Depends(get_current_user)`
- [x] Call `get_total_count(db, current_user.id)`
- [x] Return `{"count": <int>}`
- [x] Router already has `router = APIRouter(prefix="/api/notifications", tags=["notifications"])` — add route to existing router instance
- [x] No shadow-routing hazard: router has no `GET /{notification_id}`-shaped catch-all, only `PUT /{notification_id}/read`

### 3. Write tests
- [x] Create `backend/tests/test_notification_count.py`
- [x] Test: register → count returns 0
- [x] Test: register → create goal → count returns 1
- [x] Test: no auth header → 401
- [x] Test: expired/malformed token → 401
- [x] Use `ACCEPTANCE_RUN_ID`-namespaced identifiers for all test entities
- [x] Assert only status code for 401 cases (not body wording)

## Dev Notes

### Context files to load
- [Source: context/project.md]
- [Source: context/current-state.md]
- [Source: context/modules/auth.md]
- [Source: context/modules/backend.md]
- [Source: context/modules/security.md]
- [Source: context/architecture-diagrams.md]

### Pattern reference
- Copy the shape of `get_unread_count` in `backend/app/services/notification.py:82` — same async signature, same db + user_id params, same return type, different WHERE clause (no `read = false` filter).
- Route pattern: see `GET /api/notifications/unread-count` at `backend/app/routes/notifications.py:44-50`.

### api_spec.md verbatim embed

```
# API spec — notification total count

## GET /api/notifications/count **(new)**

Authenticated. No query parameters, no request body.

**Request:** `Authorization: Bearer <access_token>` header required.

**200 OK**

{
  "count": 0
}

| field | type | constraint |
|---|---|---|
| `count` | integer | the caller's own total notification count (all types, read and unread); non-negative |

**401** — no `Authorization` header, expired token, malformed token, or empty
token string. Assert status code only; do not assert the body (see "the 401
body" note in `d2.md` — this direction did not introduce `get_current_user`,
so it cannot state what that dependency's error body says).

## Setup used by the acceptance criteria (arrange, not assert)

1. `POST /api/auth/email/register` with a unique, `ACCEPTANCE_RUN_ID`-
   namespaced email and a password ≥8 chars
   (`backend/app/schemas/auth.py:4-8`, `EmailRegisterRequest`). Response `200`
   with `AuthResponse {"access_token": <str>, "user": {...}}`
   (`backend/app/routes/auth.py:55-58`). Use `access_token` as the bearer token
   for every subsequent call in this story.
2. `GET /api/notifications/count` with that token → expect `{"count": 0}`
   (criterion 1).
3. `POST /api/goals` with that token and the body:
   {
     "title": "<ACCEPTANCE_RUN_ID>-notif-count-check",
     "deadline": "<now + 7 days, ISO-8601>",
     "pledge_amount": 500,
     "goal_type": "api_endpoint",
     "criteria": {"url": "https://example.com/health", "method": "GET", "expected_status": 200}
   }
   Response `201`. This fires exactly one `goal_created` notification
   (`backend/app/routes/goals.py:139-146`), which is EXISTING behavior — no
   code change makes this happen, it already does.
4. `GET /api/notifications/count` with that token again → expect
   `{"count": 1}` (criterion 2).

## Acceptance criteria — how each is observed

### 1. A fresh caller's notification count is 0

- **Status:** graded by the acceptance oracle (HTTP)
- **How:** register (step 1 above), then `GET /api/notifications/count`.
  Assert `200` and body exactly `{"count": 0}`.
- **Endpoints:** `/api/auth/email/register`, `/api/notifications/count`

### 2. Creating a goal increases the count by exactly one

- **Status:** graded by the acceptance oracle (HTTP)
- **How:** steps 1-4 above. Assert the second `GET /api/notifications/count`
  returns `200` with body exactly `{"count": 1}`.
- **Endpoints:** `/api/auth/email/register`, `/api/goals`,
  `/api/notifications/count`

### 3. An unauthenticated request is rejected

- **Status:** graded by the acceptance oracle (HTTP)
- **How:** `GET /api/notifications/count` with no `Authorization` header.
  Assert status `401`. Do not assert the response body.
- **Endpoints:** `/api/notifications/count`

## Observability affordances and their constraints

None introduced beyond the `count` field itself, which exposes only a number
already fully derivable by the caller paging their own `GET
/api/notifications` — no new information leaks to a caller about any other
user's data.

## References

- `backend/app/routes/notifications.py` — existing router, lines 44-50 (unread-count pattern)
- `backend/app/services/notification.py` — service layer, line 82 (`get_unread_count` pattern)
- `backend/app/core/dependencies.py` — `get_current_user` dependency
- `backend/app/routes/goals.py` — lines 139-146 (goal_created notification fire)
- `backend/app/schemas/goal.py` — lines 11-21 (GoalCreate schema, no status field)
- `backend/app/services/goal.py` — line 132 (gate_criteria called unconditionally)
- `backend/app/goal_types/api_endpoint/definition.py` — criteria_schema.required
- `context/accountability-invariants.md`

## Dev Agent Record

**Completion Notes:** All acceptance criteria satisfied. Implementation added `get_total_count` to notification service following the exact pattern of `get_unread_count` (same signature, same file, different WHERE clause), added the `GET /api/notifications/count` route following the pattern of `GET /api/notifications/unread-count`, and created `test_notification_count.py` with 5 tests covering all ACs. Full suite: 1562 passed, 2 skipped, 0 failures.

**File List:**
- `backend/app/services/notification.py` — added `get_total_count` function (lines 92-98)
- `backend/app/routes/notifications.py` — added import for `get_total_count` (line 11), added `/count` route (lines 54-60)
- `backend/tests/test_notification_count.py` — new file, 5 test functions

## Senior Developer Review

(To be filled by the senior developer during review.)

## Review Follow-ups

(To be filled after review if follow-up actions are needed.)

<!-- factory:dual-draft-full-coverage -->