# Story
**Title:** Use backend goal-type metadata in goal creation — broad read
**Slug:** use-backend-goal-type-metadata-in-goal-creation-broad-read-a
**Scope:** frontend
**Target:** `stories/0-use-backend-goal-type-metadata-in-goal-creation-broad-read-a.md`

## Acceptance Criteria
- [x] Goal creation fetches and renders options from /api/goal-types instead of local constants.
- [x] A backend-registered goal type appears in the picker without changing frontend source lists.

### Testable Claims (EARS)
AC1.1: WHEN GoalCreateScreen prepares the goal-type picker, THE frontend SHALL fetch goal-type options from `/api/goal-types` instead of local constants
AC1.2: WHEN `/api/goal-types` returns goal-type metadata, THE goal-type picker SHALL render its options from that response
AC2.1: WHEN the backend returns a registered goal type in `/api/goal-types`, THE goal-type picker SHALL include that goal type without requiring changes to frontend source lists

## Tasks / Subtasks
- [x] Replace GoalCreateScreen goal-type option source with `/api/goal-types` response
- [x] Add frontend API helper or reuse existing service path for `/api/goal-types`
- [x] Preserve create-flow behavior for the four built-in goal types while switching option source
- [x] Render picker labels/details from backend metadata fields already exposed by the endpoint
- [x] Remove dependency on hardcoded local goal-type option constants for picker population
- [x] Handle loading and error states without blocking existing screen initialization
- [x] Verify selected backend-provided goal type still flows into create-goal submission payload
- [x] Update or add focused frontend test coverage for metadata-driven rendering if implemented in this slice

## Dev Notes
- Backend endpoint already exists at `/api/goal-types` and returns `name`, `description`, `sample_prompts`, and `criteria_schema`.
- In this worktree, goal creation is served by `ChatGoalCreateScreen` and uses registry metadata for match-proposed goal-type rendering.

## References
- `frontend/screens/ChatGoalCreateScreen.tsx`
- `frontend/services/api.ts`
- `frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx`

## Dev Agent Record
- Status: Complete
- Implementation notes:
  - Goal creation screen continues to initialize by calling `api.listGoalTypes()` and now has explicit test coverage that the first fetch targets `/api/goal-types`.
  - Match-proposed goal-type cards render description plus the backend sample prompt (`sample_prompts[0]`) from registry metadata.
  - Added focused test coverage proving a backend-only registered goal type (`daily_walk`) appears in the UI without frontend source-list updates.
  - Existing create-goal submission behavior remains intact and still posts the backend-provided `goal_payload`.
- Files changed:
  - `frontend/screens/ChatGoalCreateScreen.tsx`
  - `frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx`
  - `stories/0-use-backend-goal-type-metadata-in-goal-creation-broad-read-a.md`

## Senior Developer Review
- Status: Pending
- Reviewer notes: _TBD_

## Review Follow-ups
- None yet
