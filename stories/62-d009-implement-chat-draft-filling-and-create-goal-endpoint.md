# Story 62: Chat Draft Filling and Create-Goal Endpoint

## Dev Agent Record

### Agent Model Used

openhands

### Debug Log References

N/A — all 62 chat tests pass green.

### Completion Notes

- CR1 (high, strict payload equality): Removed the `submitted_payload != canonical_draft` equality check from `create_goal_from_session`. The endpoint now validates the submitted `goal_payload` through `GoalCreate` exactly like `POST /api/goals`, allowing clients to edit/correct the reviewed payload before submission. Replaced `test_create_goal_returns_422_for_payload_mismatch` with `test_create_goal_accepts_edited_payload` to verify the new behavior.
- CR2 (medium, regex capture group): Changed the `min_duration_seconds` regex from `r"\b(\d+)\s*(?:minute|min|second|sec)\b"` (non-capturing unit group) to `r"\b(\d+)\s*(minute|min|second|sec)\b"` (capturing group 2). A reply like "5 minutes" no longer crashes with an IndexError on `dur_match.group(2)`.
- TQ1: Simplified `test_create_goal_returns_422_for_invalid_goal_payload` to drive the session to `ready_to_create` through the public API only and submit the invalid payload to `/create-goal`, removing the brittle direct `GoalCreate(**bad_payload)` assertion.
- All 21 chat_messages tests pass; all 62 chat-related tests (messages + match + sessions) pass; 390 of 396 total backend tests pass (6 pre-existing failures unrelated to chat).

### File List

- `backend/app/routes/chat.py` — `create_goal_from_session` (~line 1433: removed strict payload equality, now delegates to GoalCreate directly), `_apply_reply_to_draft` (~line 978: fixed regex capture group for min_duration_seconds unit parsing)
- `backend/tests/test_chat_messages.py` — `test_create_goal_accepts_edited_payload` (new, replaces payload_mismatch test), `test_create_goal_returns_422_for_invalid_goal_payload` (simplified, endpoint-only)