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
- Added `get_draft_count` handler to `backend/app/routes/goal_count.py` (lines 32-43), following the existing `get_goal_count` pattern (same file, lines 20-29).
- Router ordering in `backend/app/main.py` already has `goal_count_router` (line 95) before `goals_router` (line 96) — no change needed.
- Test file `backend/tests/test_goal_draft_count.py` with 5 tests covering all acceptance criteria: AC1.1 (zero count for fresh user), AC2.1 (count increments after goal creation), AC3.1 (no auth header → 401), AC3.2 (expired token → 401), AC3.3 (malformed token → 401).
- Cycle 5 (this round): applied two reviewer-proposed edits:
  1. Changed malformed token from `this-is-not-a-valid-jwt` (3-part, structurally valid JWT) to `not-a-valid-jwt` (no dots, truly malformed) — `test_goal_draft_count.py:152`.
  2. Added `@pytest_asyncio.fixture` async `client` fixture using `ASGITransport` + `AsyncClient` alongside existing `make_client()` — `test_goal_draft_count.py:11-15`. Deviated from reviewer's verbatim edit: the reviewer proposed `@pytest.fixture` + `def client()` with `async with` body, which is a SyntaxError (cannot use `async with` in a non-async function). Used `@pytest_asyncio.fixture` + `async def client()` instead, matching the conftest.py precedent (`conftest.py:40` uses `pytest_asyncio`).
- DB isolation: conftest.py `test_db` fixture is already `autouse=True` and truncates tables after each test; the test file follows the same `make_client()` pattern as `test_goal_count.py`.
- All prior-cycle fixes preserved: unique runtime emails, passwords, titles; expired-token test uses real registered user.
- Full test suite: 1570 passed, 2 skipped, 0 failures (pre-existing e2e CLI failures unrelated).

**Story-silent choices:**
- `backend/app/routes/goal_count.py:38`: used `select(func.count()).select_from(Goal)` pattern matching existing `get_goal_count` (same file, line 25).
- `backend/app/routes/goal_count.py:42`: used `result.scalar_one()` to extract integer count, matching existing `get_goal_count` (same file, line 28).
- `backend/tests/test_goal_draft_count.py`: test structure (ASGITransport, make_client, direct route calls) follows existing `test_goal_count.py` pattern.
- `backend/tests/test_goal_draft_count.py`: expired token test uses `_create_signed_token` with `timedelta(minutes=-60)`, matching the same import pattern used in `test_auth.py`.
- Test emails follow `f"<purpose>-{uuid.uuid4().hex}@test.com"` pattern — api_spec.md arranges unique emails per run; uuid ensures no cross-run collisions.
- Test password follows `f"Ok-{uuid.uuid4().hex}"` as specified in api_spec.md arrange instructions.
- Test title follows `f"{uuid.uuid4().hex}-draft-count-check"` matching api_spec.md requirement for `ACCEPTANCE_RUN_ID`-namespaced titles.
- Test deadline: `datetime.now(timezone.utc) + timedelta(days=7)` — follows pattern from `test_goal_count.py` and `api_spec.md` arrange instructions.
- Malformed token value `not-a-valid-jwt`: reviewer-proposed edit; no precedent search needed.
- `@pytest_asyncio.fixture` + `async def client()` deviation from reviewer's sync `@pytest.fixture`: `conftest.py:40` (`import pytest_asyncio`) is the precedent for using `pytest_asyncio` in this repo; a sync function with `async with` body is a `SyntaxError`.

**File List:**
- `backend/app/routes/goal_count.py` — added `get_draft_count` handler (lines 32-43)
- `backend/tests/test_goal_draft_count.py` — 5 tests (all acceptance criteria), malformed token + fixture updates

## Senior Developer Review

(To be filled by the Senior Developer persona after implementation.)

## Review Follow-ups

(To be filled if the Senior Developer review raises action items.)