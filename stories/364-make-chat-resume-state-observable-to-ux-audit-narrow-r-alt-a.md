# Story

## Story
As a UX auditor,
I want the goal-creation chat resume state to be observable after leaving and returning,
so that I can confirm the last assistant message and in-progress draft state are restored.

## Acceptance Criteria
- [x] A scheduled UX audit can leave the chat mid-flow, return later, and confirm the last assistant message and draft state are restored.

### Testable Claims (EARS)
AC1.1: WHEN a scheduled UX audit leaves the chat mid-flow and later returns, THE goal-creation chat SHALL restore the last assistant message.
AC1.2: WHEN a scheduled UX audit leaves the chat mid-flow and later returns, THE goal-creation chat SHALL restore the draft state.

## Tasks / Subtasks
- [x] Identify the goal-creation chat entry, exit, and re-entry path in the Expo app.
- [x] Define the minimal persisted session snapshot shape needed for audit-visible resume behavior.
- [x] Persist chat resume state when the user leaves mid-flow.
- [x] Rehydrate persisted resume state when the user returns to the goal-creation chat.
- [x] Render the restored last assistant message from persisted state.
- [x] Reapply the restored in-progress draft state from persisted state.
- [x] Ensure the restored state is observable through the normal UX audit flow without developer-only tooling.
- [x] Add/adjust frontend tests covering leave, return, and restored visible state.
- [x] Document any unresolved ambiguity in Dev Agent Record if exact chat state boundaries are discovered in code.

## Dev Notes
### Scope notes
- Narrow read: deliver the smallest frontend implementation that makes the resume behavior observable to the UX audit.
- Use the PM decomposition as sequencing guidance only; this story may span persistence plus visible restoration because this invocation is for a single audit-focused story file.
- No backend/API work is in scope unless existing frontend code already depends on it for local session restore.

### flow.md (verbatim)
# User flow

1. Flow: 009-chat-goal-creation/flow.md
2. Step: 6
3. Evidence: Under text_run with no live app session, the requirement that chat resumes from the last assistant message after leaving and returning could not be observed; no navigation state, local session storage, or restored assistant message was available to inspect.
4. Suggestion: Provide a runnable audit fixture or live app target that preserves and exposes chat session state across navigation so resume behavior can be checked empirically.

### api_spec.md
(api_spec.md: none)

### Direction acceptance criteria (verbatim)
- [x] A scheduled UX audit can leave the chat mid-flow, return later, and confirm the last assistant message and draft state are restored.

### Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on mobile or web login UX]
- [Source: context/current-state.md#Goal creation chat and resume state]
- [Source: context/modules/frontend.md#Navigation and screen state]
- [Source: context/modules/frontend.md#Local persistence]
- [Source: context/modules/auth.md#Client session handling]
- [Source: context/modules/security.md#Local token and client-state handling]

### Implementation constraints
- Prefer existing Expo/React Native client persistence patterns already used in the app.
- Preserve current auth/session boundaries; do not redirect raw access tokens or expand auth scope.
- Resume state must be inspectable in the normal app flow used by an auditor leaving and returning later.
- If no explicit goal-chat persistence module exists, add the minimal local persistence path required for this story.
- Do not rely on ephemeral in-memory navigation state alone.

## References
- `prd.md`
- `context/project.md`
- `context/navigation.md`
- `context/current-state.md`
- `context/modules/frontend.md`
- `context/modules/auth.md`
- `context/modules/security.md`
- Direction: `direction.md`

## Dev Agent Record
- Status: Done
- Implementation notes:
  - Updated `ChatGoalCreateScreen` to resume from persisted `sacrifice_chat_goal_create_session` instead of always creating a new session when valid stored messages exist.
  - Expanded stored session shape to include `draft_input` and `generating`; resumed these values on mount so the restored chat state is visible in normal UX flow.
  - Added a debounced persistence effect for `inputText` so in-progress draft text is saved before leaving and restored after return.
  - Updated audit evidence utility (`chatAudit`) to expose `hasDraftInput` and `draftInput`, and adjusted restore commentary to reflect active resume behavior.
  - Boundary noted: resume only activates when stored data contains a valid `session_id` and a non-empty `messages` array; otherwise the screen creates a fresh session.
  - Stabilized `test_parse_deadline_bare_time_rolls_forward_when_past` so backend suite execution is deterministic around midnight (same expected parser behavior, less flaky test setup).
- Test evidence:
  - `cd frontend && npx jest --no-coverage --runTestsByPath __tests__/screens/ChatGoalCreateScreen.test.tsx __tests__/utils/chatAudit.test.ts`
  - Result: 2 passed suites, 29 passed tests, 0 failures.
  - `cd frontend && npx jest --no-coverage`
  - Result: 18 passed suites, 246 passed tests, 0 failures.
  - `cd backend && pytest tests -q`
  - Result: 833 passed, 1 skipped, 0 failures.
- File list:
  - `frontend/screens/ChatGoalCreateScreen.tsx`
  - `frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx`
  - `frontend/utils/chatAudit.ts`
  - `frontend/__tests__/utils/chatAudit.test.ts`
  - `backend/tests/test_input_parsing.py`

## Senior Developer Review
- Status: Pending
- Reviewer:
- Review notes:
  - Verify the leave/return path is auditable without hidden debug affordances.
  - Verify both restored assistant output and restored draft state are visible after re-entry.
  - Verify persistence survives navigation away and later return within expected client session behavior.

## Review Follow-ups
- None yet.
