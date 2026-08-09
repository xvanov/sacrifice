# Story: D121: Add GET /api/goals/recurrence-options endpoint + tests

## Story

Add a single static, unauthenticated `GET /api/goals/recurrence-options` endpoint that returns the canonical list of recurrence values the schema accepts: `["none", "daily", "weekly", "monthly"]`. This is reference data — no database access, no auth dependency, no per-user variation. The frontend can call this before login to populate recurrence-picker controls without hard-coding the enum.

Register the route on `backend/app/routes/goal_count.py`'s existing `APIRouter(prefix="/api/goals")` — NOT on `goals_router` — to avoid the `/{goal_id}` catch-all routing hazard documented in References.

Create exactly one test file covering all three acceptance criteria.

This is the ONLY story for this direction (no sibling).

## Acceptance Criteria

- [x] `GET /api/goals/recurrence-options` returns `200` with a JSON body whose `options` field is exactly the array `["none", "daily", "weekly", "monthly"]` — the same four values `POST /api/goals` and `PUT /api/goals/{id}` accept for `recurrence` (`backend/app/schemas/goal.py:55`, `:98`).
- [x] The endpoint answers identically without any `Authorization` header: an unauthenticated `GET /api/goals/recurrence-options` also returns `200` with the same `options` body, so the frontend can populate the control before login exists.
- [x] Sending a garbage `Authorization` header (e.g. `Bearer not-a-real-token`) does not change the response: it still returns `200` with the identical `options` body, never a `401` — demonstrating this is genuinely public reference data, not an authenticated route that happens to tolerate a bad token.

## Tasks/Subtasks

- [x] Add `get_recurrence_options` handler to `goal_count.py` router
  - [x] Add route `GET /recurrence-options` (registered on existing `APIRouter(prefix="/api/goals")`)
  - [x] Return `{"options": ["none", "daily", "weekly", "monthly"]}` as a static literal
  - [x] No dependencies injected — no `db: Session`, no `current_user`
  - [x] Add no decorators (`@router.get(...)` only)
- [x] Create test file `backend/tests/test_goal_recurrence_options.py`
  - [x] Test: happy-path 200 with exact options array (no auth header)
  - [x] Test: unauthenticated request returns 200 (explicit no-header assertion)
  - [x] Test: garbage `Authorization: Bearer not-a-real-token` returns 200, never 401
- [x] Verify green across all three tests

## Dev Agent Record

**Completion Notes:**
- Added `get_recurrence_options` handler to `backend/app/routes/goal_count.py` on the existing `APIRouter(prefix="/api/goals")`, placed _before_ the `/count` route. No dependencies, no decorators beyond `@router.get("/recurrence-options")`. Returns static `{"options": RECURRENCE_OPTIONS}` with `RECURRENCE_OPTIONS = ["none", "daily", "weekly", "monthly"]`.
- Route is registered on `goal_count.py`'s router (included at `main.py:95`, before `goals_router` at `main.py:96`), avoiding the `/{goal_id}` catch-all routing hazard.
- Created `backend/tests/test_goal_recurrence_options.py` with three test functions following the `make_client()` pattern from `test_goal_count.py:5-7`. All three tests pass.
- Values verified against the single source of truth: `backend/app/schemas/goal.py:55` (`GoalCreate.validate_recurrence`), `:98` (`GoalUpdate.validate_recurrence`), and `backend/app/models/goal.py:27-31` (DB enum).
- No changes to `backend/app/main.py` — the router was already included at line 95.
- Full suite confirmed green: 3/3 new tests pass, 0 pre-existing tests regress.

**File List:**
- `backend/app/routes/goal_count.py` — added `RECURRENCE_OPTIONS` constant (line 12) and `get_recurrence_options` route handler (lines 15-17)
- `backend/tests/test_goal_recurrence_options.py` — new test file (3 tests, 49 lines)

## Senior Developer Review

_To be filled by the reviewing agent._

## Review Follow-ups

_To be filled if review identifies blocking issues or required changes._