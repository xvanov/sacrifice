# Story 62: Chat Draft Filling and Create-Goal Endpoint

## Dev Agent Record

### Agent Model Used

openhands

### Debug Log References

N/A — all 62 chat tests pass green.

### Completion Notes

- CR1 (high, create-goal source of truth): `create_goal_from_session` now ignores the request body entirely and creates the goal exclusively from the stored server-side `ready_to_create` draft in the session messages. This prevents clients from substituting an arbitrary payload; the conversationally collected draft is the only source of truth.
- CR2 (high, ready-state scan): Fixed the backward scan in `create_goal_from_session` — the `break` now lives inside the `if` block that checks for `ready_to_create`, so the scan properly skips over intervening edit-prompt assistant messages and finds the most recent `ready_to_create` action even after an edit turn.
- TQ1: Replaced `test_create_goal_rejects_unreviewed_payload` with `test_create_goal_ignores_client_body_and_uses_stored_draft` which proves the endpoint ignores tampered client payloads (different title, different pledge) and creates from the stored draft. Also updated `test_create_goal_accepts_canonical_normalization_differences` → `test_create_goal_accepts_format_variations_in_body` to reflect body-is-ignored semantics, and rewrote `test_create_goal_returns_422_for_invalid_goal_payload` → `test_create_goal_returns_422_for_invalid_stored_draft` to corrupt the server-side draft directly since the body is no longer validated.
- All 22 chat_messages tests pass; all 63 chat tests (sessions + match + messages) pass; 391 of 397 backend tests pass (6 pre-existing failures unrelated to chat: youtube_verification, api_endpoint_verification, goal_type_smoke, notifications).

### File List

- `backend/app/routes/chat.py` — `create_goal_from_session` (~line 1427: body-ignored extraction from stored ready_to_create with correct break-in-if scan)
- `backend/tests/test_chat_messages.py` — `test_create_goal_ignores_client_body_and_uses_stored_draft`, `test_create_goal_accepts_format_variations_in_body`, `test_create_goal_returns_422_for_invalid_stored_draft`