# Story: D123 Add unread_notifications field to dashboard stats + acceptance test

## Story

As a dashboard consumer, I want `GET /api/dashboard/stats` to include an `unread_notifications` field so that I can render a notification badge without a second round-trip to `/api/notifications/unread-count`.

**Scope:** backend — one handler field addition + one test file extension. No new files. Two modified files: `backend/app/routes/dashboard.py` and `backend/tests/test_dashboard.py`.

**Key constraint:** Additive-only. No existing response field changes value or computation. `GET /api/notifications/unread-count` is not modified. `GET /api/dashboard/history` is not touched.

## Acceptance Criteria

- [x] Immediately after registering a new account (no goals, no notifications yet for that account), `GET /api/dashboard/stats` returns `200` with a new `unread_notifications` field equal to `0`, alongside the existing response fields (`total_goals`, `completed_count`, `failed_count`, `success_rate`, `total_pledged`, `total_donated`, `total_saved` — `backend/app/routes/dashboard.py:64-71` — all unchanged in shape and computation).
- [x] After that same caller creates one goal via `POST /api/goals`, `GET /api/dashboard/stats`'s `unread_notifications` field equals `1` (the automatic `goal_created` notification, unread by default — `backend/app/models/notification.py:34`, `read: Mapped[bool] = mapped_column(Boolean, default=False)`) **while** `total_goals` in the same response also equals `1` — confirming the new field did not disturb the existing ones it sits beside.
- [x] `unread_notifications` in `GET /api/dashboard/stats` exactly matches the value simultaneously obtainable from the existing, unmodified `GET /api/notifications/unread-count` for the same caller — asserting the new field is a read of the same source of truth (`get_unread_count`, `backend/app/services/notification.py:82`), not a second, independently-computed count that can drift from it.

### Testable Claims (EARS)

AC1.1: WHEN a freshly registered caller with zero goals requests `GET /api/dashboard/stats`, GIVEN no notifications exist for that caller, THE `unread_notifications` field in the response SHALL equal `0`.
AC1.2: WHEN a freshly registered caller with zero goals requests `GET /api/dashboard/stats`, THE response SHALL contain all existing fields (`total_goals`, `completed_count`, `failed_count`, `success_rate`, `total_pledged`, `total_donated`, `total_saved`) with their pre-existing computation unchanged.
AC2.1: WHEN a caller who has created exactly one goal requests `GET /api/dashboard/stats`, GIVEN the automatic `goal_created` notification is unread by default, THE `unread_notifications` field SHALL equal `1`.
AC2.2: WHEN a caller who has created exactly one goal requests `GET /api/dashboard/stats`, THE `total_goals` field in the same response SHALL equal `1`.
AC3.1: WHEN any caller requests `GET /api/dashboard/stats` and then immediately requests `GET /api/notifications/unread-count` with the same bearer token, THE `unread_notifications` field from the dashboard response SHALL exactly equal the `unread_count` field from the unread-count response.

## Tasks/Subtasks

- [x] Task 1: Import `get_unread_count` into `backend/app/routes/dashboard.py`
  - [x] Add `from backend.app.services.notification import get_unread_count` alongside existing imports
- [x] Task 2: Add `unread_notifications` field to `get_dashboard_stats` return dict
  - [x] Call `get_unread_count` with `user_id=current_user.id` and `db=db`
  - [x] Assign result to `unread_notifications` key in the return dict at line ~71
- [x] Task 3: Extend existing test in `backend/tests/test_dashboard.py`
  - [x] Add test for AC1: fresh account → `unread_notifications == 0`
  - [x] Add test for AC2: after goal creation → `unread_notifications == 1` AND `total_goals == 1`
  - [x] Add test for AC3: `unread_notifications` matches `GET /api/notifications/unread-count`'s `unread_count`
- [x] Task 4: Run full test suite to confirm no regressions
  - [x] `pytest backend/tests/test_dashboard.py -v`
  - [x] `pytest backend/tests/test_notifications.py -v` (sanity — no changes expected)

## Dev Notes

### Context files
- [Source: context/modules/backend.md]
- [Source: context/modules/auth.md]
- [Source: context/current-state.md]

### api_spec.md embed

## GET /api/dashboard/stats **(existing — additive change)**

Authenticated. No query parameters, no request body. Unchanged request shape.

**Request:** `Authorization: Bearer <access_token>` header required (existing behavior, unchanged).

**200 OK — new field added, all existing fields unchanged**

```json
{
  "total_goals": 1,
  "completed_count": 0,
  "failed_count": 0,
  "success_rate": 0.0,
  "total_pledged": 500,
  "total_donated": 0,
  "total_saved": 0,
  "unread_notifications": 1
}
```

| field | type | status |
|---|---|---|
| `total_goals` | integer | existing, unchanged |
| `completed_count` | integer | existing, unchanged |
| `failed_count` | integer | existing, unchanged |
| `success_rate` | float | existing, unchanged |
| `total_pledged` | integer (cents) | existing, unchanged |
| `total_donated` | integer (cents) | existing, unchanged |
| `total_saved` | integer (cents) | existing, unchanged |
| `unread_notifications` | integer | **new** — the caller's unread notification count, identical to `GET /api/notifications/unread-count`'s `unread_count` field for the same caller |

**401** — existing, unchanged behavior (`get_current_user` dependency). Not introduced by this direction; not asserted in this direction's criteria.

## Setup used by the acceptance criteria (arrange, not assert)

1. `POST /api/auth/email/register` with a unique, `ACCEPTANCE_RUN_ID`- namespaced email. Response `200` with `AuthResponse {"access_token": <str>, "user": {...}}`. Use `access_token` as the bearer token for every subsequent call.
2. `GET /api/dashboard/stats` with that token → expect `unread_notifications == 0`, `total_goals == 0` (criterion 1).
3. `POST /api/goals` with that token and the body:
   ```json
   {
     "title": "<ACCEPTANCE_RUN_ID>-dashboard-unread-check",
     "deadline": "<now + 7 days, ISO-8601>",
     "pledge_amount": 500,
     "goal_type": "api_endpoint",
     "criteria": {"url": "https://example.com/health", "method": "GET", "expected_status": 200}
   }
   ```
   Response `201`. Fires one `goal_created` notification (existing behavior, `backend/app/routes/goals.py:139-146`), unread by default.
4. `GET /api/dashboard/stats` with that token again → expect `unread_notifications == 1` and `total_goals == 1` (criterion 2).
5. `GET /api/notifications/unread-count` with the same token → expect `unread_count == 1`, and assert it equals step 4's `unread_notifications` (criterion 3).

## Acceptance criteria — how each is observed

### 1. A fresh caller's `unread_notifications` is 0

- **Status:** graded by the acceptance oracle (HTTP)
- **How:** register (step 1), then `GET /api/dashboard/stats`. Assert `200` and `unread_notifications == 0`.
- **Endpoints:** `/api/auth/email/register`, `/api/dashboard/stats`

### 2. Creating a goal increases `unread_notifications` by exactly one, without disturbing `total_goals`

- **Status:** graded by the acceptance oracle (HTTP)
- **How:** steps 1-4. Assert the second `GET /api/dashboard/stats` call returns `unread_notifications == 1` AND `total_goals == 1` in the same response.
- **Endpoints:** `/api/auth/email/register`, `/api/goals`, `/api/dashboard/stats`

### 3. `unread_notifications` matches `GET /api/notifications/unread-count`'s value for the same caller

- **Status:** graded by the acceptance oracle (HTTP)
- **How:** step 5. Assert `GET /api/notifications/unread-count`'s `unread_count` field equals the `unread_notifications` value obtained in step 4, for the same access token.
- **Endpoints:** `/api/dashboard/stats`, `/api/notifications/unread-count`

## Observability affordances and their constraints

`unread_notifications` exposes only a count the caller can already obtain from `GET /api/notifications/unread-count`. No new information is exposed to any caller about any other user's data, and no existing field's value or type changes.

### Scope-specific notes
- `backend/app/routes/dashboard.py:14-15` — `get_dashboard_stats` has no Pydantic `response_model`; returns a plain `dict`. No schema update required.
- `get_unread_count(user_id, db)` is already defined at `backend/app/services/notification.py:82` and already imported in `backend/app/routes/notifications.py`. Follow the same import pattern.
- `get_dashboard_stats` receives `current_user: User = Depends(get_current_user)` — use `current_user.id` as the `user_id` argument.
- The handler's existing queries (`get_active_goal_count`, `get_completed_goal_count`, etc.) are all read-only. Adding one more read-only aggregation (`get_unread_count`) does not introduce side effects.
- Test file `backend/tests/test_dashboard.py` already tests `get_dashboard_stats`. Extend the existing test or add new test functions following the file's existing pattern and the `api_spec.md` arrange-act-assert sequence.

## References
- Direction: `direction.md` for D123 (add unread-notification count to dashboard stats)
- Blast radius measurement: `GET /api/dashboard/stats` referenced in exactly 1 test file (`backend/tests/test_dashboard.py`)
- Service function: `get_unread_count` at `backend/app/services/notification.py:82`
- Model: `Notification.read` defaults to `False` — `backend/app/models/notification.py:34`
- Related endpoint (not modified): `GET /api/notifications/unread-count` at `backend/app/routes/notifications.py:44-50`
- Prior direction lesson: Direction 117 (POST /api/goals change) failed with ~40 sibling tests broken; this direction pre-measured blast radius to avoid that

## Dev Agent Record

- Agent: Amelia (dev)
- Status: Complete — all ACs satisfied, all tests green. Two reviewer cycles addressed.
- Notes:
  - Imported `get_unread_count` from `app.services.notification` in `backend/app/routes/dashboard.py` (line 10), following the same import pattern used in `backend/app/routes/notifications.py:12`.
  - Added `unread_notifications = await get_unread_count(db, user_id)` call at `backend/app/routes/dashboard.py:65` and the corresponding key at line 75 in the return dict.
  - Extended `backend/tests/test_dashboard.py` with:
    - `test_unread_notifications_zero_for_fresh_email_account` — AC1: registers via email, checks `unread_notifications == 0` and all existing fields unchanged.
    - `test_unread_notifications_increments_after_goal_creation` — AC2: creates a goal, checks `unread_notifications == 1` AND `total_goals == 1`.
    - `test_unread_notifications_matches_unread_count_endpoint` — AC3: compares dashboard `unread_notifications` against `/api/notifications/unread-count`'s `unread_count`.
  - Updated `test_dashboard_stats_returns_zero_for_no_goals` to also assert `unread_notifications == 0`.
  - All 29 dashboard+notification tests pass (13 dashboard + 16 notification).
  - **Reviewer cycle 1 (high/correctness — swap arg order) DECLINED:** The reviewer claimed `get_unread_count` signature is `get_unread_count(user_id, db)`, but the actual signature at `backend/app/services/notification.py:82` is `get_unread_count(db: AsyncSession, user_id: uuid.UUID)` — `db` first. The call at `dashboard.py:65` (`get_unread_count(db, user_id)`) and the pre-existing call at `notifications.py:49` (`get_unread_count(db, current_user.id)`) both match the real signature. No change needed.
  - **Reviewer cycle 2 (high/lint — ruff violations) FIXED:**
    - `backend/app/routes/dashboard.py:2`: Removed unused `text` from `from sqlalchemy import func, select, text`.
    - `backend/tests/test_dashboard.py:428-430`: Removed unused variable assignments `resp1`, `resp2`, `resp3` in `test_dashboard_history_returns_goals_sorted_by_creation_date` — replaced with bare `await _create_goal(...)` calls since only the side effects are needed.
    - Ruff clean on all changed files.
  - Story-silent choices:
    - Import path `from app.services.notification import get_unread_count` matches the pattern in `backend/app/routes/notifications.py:12` (`from app.services.notification import get_unread_count`). No `backend.` prefix — the repo uses `app.` imports consistently in route files (`backend/app/routes/dashboard.py:3-9`).
    - New test naming follows existing convention: `test_dashboard_stats_returns_*` (sibling tests) → `test_unread_notifications_*`.
    - Email registration test pattern (unique email per test, register → token → API call → assert) matches the existing `test_dashboard_stats_returns_*` tests in the same file that use `make_client()` and `POST /api/auth/email/register`.
    - `import os`, `import uuid as _uuid`, and `from datetime import datetime, timedelta, timezone` are placed inside test functions (not top-level), following the existing convention in the same file.
    - `ACCEPTANCE_RUN_ID` env var default `"d123"` — story specifies the concept, no precedent for the exact default value; the value follows the direction number.
    - Removing unused `resp1`/`resp2`/`resp3` in pre-existing test `test_dashboard_history_returns_goals_sorted_by_creation_date` at `backend/tests/test_dashboard.py:428-430` — these were pre-existing lint violations caught by the ruff check on changed files. Fixing them follows the reviewer's directive to resolve all ruff violations on changed files. Precedent for bare `await`-for-side-effects pattern: the same test file uses `await _auth(client)` without capturing the full return at line 426.

- File List:
  - `backend/app/routes/dashboard.py` (modified — import fix + field)
  - `backend/tests/test_dashboard.py` (modified — 3 unused variable fixes + 1 existing test updated + 3 new test functions)

## Senior Developer Review

<!-- agent: senior-dev will populate -->

## Review Follow-ups

<!-- agent: dev will populate -->

<!-- factory:dual-draft-full-coverage -->