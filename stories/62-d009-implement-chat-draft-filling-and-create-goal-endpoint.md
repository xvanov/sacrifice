# Story 62: Chat Draft Filling and Create-Goal Endpoint

## Dev Agent Record

### Agent Model Used

openhands

### Debug Log References

N/A — all 23 chat_messages tests pass green; all 64 chat tests (match + messages + sessions) pass; 392 of 398 non-pre-existing-failure tests pass.

### Completion Notes

- CR1 (high, client payload acceptance): `create_goal_from_session` validates and creates from `body.goal_payload` (the client-submitted payload) rather than the stored `last_ready_payload`. The `last_ready_payload` dead variable has been removed — the gate now checks only that the latest assistant action type is `ready_to_create`. Presentation fields (title, description, deadline, pledge_amount) MAY differ from the stored draft — that is the point of final review. Identity consistency checks enforce: (a) `goal_type` must match the draft's matched type, and (b) all type-required criteria fields must be present in the submitted `goal_payload`. Both mismatches → 422 with clear detail.
- CR2/TQ1 (test-quality): Deleted `test_create_goal_ignores_client_body_and_uses_stored_draft`. Added `test_create_goal_accepts_client_edited_presentation_fields` (proves edited title/pledge/description are persisted from the client-submitted payload) and `test_create_goal_rejects_mismatched_goal_type` (proves identity field mismatch → 422 with goal_type in detail).
- TQ2 (test-quality): `test_create_goal_returns_422_for_invalid_goal_payload` now drives a session to ready_to_create, POSTs an invalid `goal_payload` (pledge_amount=0) to the endpoint, and asserts the endpoint returns 422.
- All 23 chat_messages tests pass; all 64 chat tests (match + messages + sessions) pass; 392 of 398 non-pre-existing-failure tests pass (6 pre-existing failures unrelated to chat: youtube_verification, api_endpoint_verification, goal_type_smoke, notifications).

### File List

- `backend/app/routes/chat.py` — `create_goal_from_session` (~line 1433: ready_to_create gate using action type string check; ~line 1448: client payload extraction and GoalCreate validation; ~line 1464: goal_type consistency check; ~line 1475: criteria fields completeness check)
- `backend/tests/test_chat_messages.py` — `test_create_goal_accepts_client_edited_presentation_fields`, `test_create_goal_rejects_mismatched_goal_type`, `test_create_goal_returns_422_for_invalid_goal_payload` (endpoint-level rewrite)