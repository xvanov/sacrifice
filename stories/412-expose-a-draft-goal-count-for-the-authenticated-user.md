# Story: Expose a draft goal count for the authenticated user

## Story

As an authenticated user, I want `GET /api/goals/draft-count` to return how many of my goals are in `draft` status, so that client-side nudges can surface unfinished goal setup without paging the full goals list.

A new route handler `get_draft_count` is added to the existing `goal_count_router` in `backend/app/routes/goal_count.py`.

## Acceptance Criteria

- [x] Immediately after registering a new account (no goals yet created for that account), `GET /api/goals/draft-count` returns `200` with body `{"count": 0}` for that caller.
- [x] After that same caller creates exactly one goal via `POST /api/goals` (which creates it in `draft` status by default), `GET /api/goals/draft-count` for that caller returns `200` with body `{"count": 1}`.
- [x] An unauthenticated `GET /api/goals/draft-count` is rejected rather than returning any count (assert status `401` only).

## Tasks / Subtasks

- [x] Add `get_draft_count` async handler to `backend/app/routes/goal_count.py`
- [x] Register the route on `goal_count_router` as `GET /draft-count`
- [x] Verify ordering: `goal_count_router` is included before `goals_router` in `backend/app/main.py`
- [x] Run existing test suite (`test_goal_count.py`) to confirm no regressions

## Dev Agent Record

**Completion Notes:**
- Added `get_draft_count` handler to `backend/app/routes/goal_count.py` (lines 32-43), following the existing `get_goal_count` pattern (same file, lines 18-29).
- Router ordering in `backend/app/main.py` already has `goal_count_router` (line 95) before `goals_router` (line 96) — no change needed.
- Test file `backend/tests/test_goal_draft_count.py` created with 5 tests covering all acceptance criteria: AC1.1 (zero count for fresh user), AC2.1 (count increments after goal creation), AC3.1 (no auth header → 401), AC3.2 (expired token → 401), AC3.3 (malformed token → 401).
- Red-first discipline: commented out the route handler, ran tests (2 authenticated tests failed with 404, 3 auth tests passed), then restored the handler (all 5 green).
- Full test suite: 1565 passed, 2 skipped, 0 failures.

**Story-silent choices:**
- `backend/app/routes/goal_count.py:38`: used `select(func.count()).select_from(Goal)` pattern matching existing `get_goal_count` (same file, line 25).
- `backend/app/routes/goal_count.py:42`: used `result.scalar_one()` to extract integer count, matching existing `get_goal_count` (same file, line 28).
- `backend/tests/test_goal_draft_count.py`: test structure (ASGITransport, make_client, direct route calls) follows existing `test_goal_count.py` pattern.
- `backend/tests/test_goal_draft_count.py`: expired token test uses `_create_signed_token` with `timedelta(minutes=-60)`, matching the same import pattern used in `test_auth.py`.
- Test emails: `draft-count-zero@test.com`, `draft-count-one@test.com` — no precedent for naming; these are descriptive guesses.
- Test password: `Ok-c0rrect-horse-battery` — follows pattern from existing tests that use `Ok-{uuid.uuid4().hex}` but with a fixed value for reproducibility.
- Test deadline: `datetime.now(timezone.utc) + timedelta(days=7)` — follows pattern from `test_goal_count.py` and `api_spec.md` arrange instructions.

## Senior Developer Review

(To be filled by the Senior Developer persona after implementation.)

## Review Follow-ups

(To be filled if the Senior Developer review raises action items.)