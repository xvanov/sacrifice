# Story

## Title
D010 direction writer persists synthesized directions to volume

## Story
As the backend,
I want synthesized directions written to the mounted factory volume with correct directory semantics,
so that the factory chain can discover and process new generation requests.

## Dev Notes (operator, 2026-06-12)

- **Build on the merged base:** `app/services/direction_synth.py` (including
  `write_direction` and `synthesize_direction`) and the related endpoints are
  ALREADY ON MAIN. Do NOT recreate the service, models, migrations, or
  re-test the whole generation flow — sibling stories own those. Implement
  ONLY what the ACs below require beyond the merged code (the
  volume-persistence specifics and focused writer tests), as additions to
  the existing module.

## Acceptance Criteria
- The `POST /api/chat/sessions/{session_id}/request-new-goal-type` endpoint (stubbed in D009) is implemented:
  - Backend writes the synthesized direction directory to a configurable path (default mounted at `/var/factory/directions/` inside the Sacrifice container; bound to `~/software-factory/apps/sacrifice/directions/` on the host).
- A new endpoint `POST /api/chat/sessions/{session_id}/iterate-generated-type` files a **new** Sacrifice direction with the following shape:
  - Frontmatter carries `parent_direction: <previous-id>-<previous-slug>` (e.g. `011-pushup-counter`). This is the canonical chain linkage; it is NOT encoded in the new direction's id or slug.
  - The new direction's id is whatever the global counter allocates — it MAY be `012`, or it may be far higher if other concurrent directions landed in between. The synthesis service does not assume sequentiality.
  - The new direction's slug describes the FEEDBACK substantively (e.g. `pushup-counter-side-angle`, `pushup-counter-half-rep-credit`). The slug does not encode chain position; `iterate-N` style slugs are explicitly forbidden because they break under concurrent allocation.
  - Why prose references the previous direction by id-slug ("This iterates on 011-pushup-counter to ...").

## Tasks / Subtasks
- Add direction writer service for configurable base path.
- Allocate global direction id from mounted directory state.
- Create directory layout expected by factory.
- Write `direction.md` and optional `flow.md` / `api_spec.md`.
- Support iteration frontmatter with `parent_direction`.
- Preserve non-sequential global id behavior.
- Add filesystem-focused tests with temp dirs.

## Dev Agent Record
- Status: Complete
- Notes:
  - All 379 tests pass (excluding 16 pre-existing failures/e2e errors in unrelated files confirmed present on clean branch).
  - Reviewer CR#1 (directory-state id allocation): Added `_next_direction_id()` helper in `direction_synth.py` that uses a counter file with `flock` for atomicity AND scans existing direction directories, reconciling `max(dir_ids, counter) + 1` as the new id. Counter file is best-effort; directory scan is the resilient fallback. `allocate_direction_id()` now delegates to `_next_direction_id()` for the starting counter.
  - Reviewer TQ#1: Added 3 tests to `tests/services/test_direction_synth.py`: `test_next_direction_id_derives_from_existing_directories` (pre-populates dirs with ids 005, 017, 042, counter=3, asserts next=043), `test_next_direction_id_starts_at_1_with_empty_volume`, `test_next_direction_id_ignores_counter_when_dirs_have_higher_ids` (dir=101, counter=5, asserts next=102).
  - Reviewer TQ#2: Added 2 tests to `tests/test_goal_generation_lifecycle.py`: `test_iterate_unknown_session_returns_404` (random UUID, asserts 404 with "session not found") and `test_request_new_goal_type_whitespace_session_id_returns_404` (URL-encoded space, asserts 404 with "session not found").
  - Implementation files: `backend/app/services/direction_synth.py`
  - Test files: `backend/tests/services/test_direction_synth.py`, `backend/tests/test_goal_generation_lifecycle.py`

## Senior Developer Review
- Pending

## Review Follow-ups
- None