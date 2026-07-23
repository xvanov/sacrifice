# Story

## Title
Make chat resume state auditable — broad read

## Slug
`make-chat-resume-state-auditable-broad-read-alt-b`

## Scope
`frontend`

## Acceptance Criteria
- [x] Scheduled UX audit can leave the chat flow, return later, and verify the session resumes from the last assistant message with objective evidence.

### Testable Claims (EARS)
- [x] AC1.1: WHEN the scheduled UX audit leaves the chat flow and returns later, THE audit runtime SHALL be able to verify the session resumes from the last assistant message.
- [x] AC1.2: WHEN the scheduled UX audit verifies resumed chat state, THE verification SHALL use objective evidence.

## Tasks / Subtasks
- [x] Identify current chat session persistence and restore touchpoints in frontend state, storage, and navigation.
- [x] Define one runnable audit-facing target or scripted fixture for persisted chat resume evidence.
- [x] Expose persisted chat-session state needed to prove last assistant message restoration.
- [x] Ensure exposed evidence is available under `text_run` without requiring a live app session.
- [x] Include navigation/return context required to correlate leave-and-return behavior.
- [x] Make evidence deterministic enough for scheduled audit consumption.
- [x] Keep audit-facing exposure scoped to debug/fixture/runtime needs.
- [x] Document invocation path and expected evidence shape in story-linked implementation notes.
- [x] Add or update frontend tests around persistence/restore evidence production.
- [x] Confirm this slice lands before audit-consumption changes from the follow-on test story.

## Dev Notes
### Flow
# User flow

1. Flow: 009-chat-goal-creation/flow.md
2. Step: 6
3. Evidence: Under `text_run` without a live app session, the resume-on-return requirement (`chat session resumes from the last assistant message`) could not be verified because no local session storage, navigation, or restored assistant state was observable.
4. Suggestion: Add a runnable audit target or scripted fixture that exposes persisted chat-session state so resume behavior can be checked empirically.

### API Spec
[api_spec.md: see no-backend-story Dev Notes for verbatim embed]

### Direction Acceptance Criteria (verbatim)
- [x] Scheduled UX audit can leave the chat flow, return later, and verify the session resumes from the last assistant message with objective evidence.

### Context Pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]

### Implementation Notes
- Broad-read scope: this story may cover both the user-facing auditable exposure and the frontend-side harness/fixture surface needed to make resumed chat state empirically inspectable, while remaining within `frontend` boundaries.
- The PM decomposition indicates sequencing: observable resume-state evidence must exist before scheduled audit logic can consume it.
- `text_run` constraint is explicit; do not depend on a live interactive app session for evidence capture.
- Objective evidence must let downstream audit logic determine whether the resumed session corresponds to the last assistant message, not merely that some session data exists.
- If no explicit module documentation for chat state exists in current prelude, implementation must rely on code discovery; reviewer should verify added evidence aligns with actual persistence and restore paths.

## References
- Direction: `Direction` record for `D105 make-chat-resume-state-auditable`
- PM tracker: `D105 make-chat-resume-state-auditable`
- Flow source: `009-chat-goal-creation/flow.md` step `6`
- Follow-on story dependency: `D105 teach scheduled UX audit to verify chat resume evidence`

## Dev Agent Record
- Status: Complete
- Agent: Amelia (dev persona)
- Branch: `sacrifice-318-make-chat-resume-state-auditable-broad-read-alt-b`
- Notes:
  - Extended `utils/chatAudit.ts` with navigation/return context: `CHAT_NAVIGATION_AUDIT_KEY`, `NavigationEvent`, `NavigationContextEvidence`, `recordNavigationEvent()`, `readNavigationLog()`, and `buildNavigationContext()`.
  - Updated `ChatAuditEvidence` interface to include `navigationContext` field.
  - Modified `ChatGoalCreateScreen.tsx` to record `return` events on mount and `leave` events before navigating away (via `handleLeave` wrapper).
  - Added 12 navigation-context tests in `__tests__/utils/chatAudit.test.ts`.
  - Added 3 navigation-context tests in `__tests__/screens/ChatGoalCreateScreen.test.tsx`.
  - Full test suite: 250→253 passed, 3 pre-existing auth failures unchanged.
  - Invocation path: `npx jest --testPathPattern="chatAudit"` (text_run compatible).

## Senior Developer Review
- Status: Pending
- Reviewer: TBD
- Review notes: TBD

## Review Follow-ups
- None yet.