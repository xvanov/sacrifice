# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record
- Status: Complete (draft-filling state machine and create-goal endpoint added)
- Agent model: openhands
- Debug log references: N/A — all 61 chat tests pass green
- Completion notes:
  - Extended `send_message` so matched sessions auto-emit an `awaiting_input` prompt for the first missing criterion after `match_proposed`, advancing the state machine immediately to criterion filling.
  - Draft-filling state machine: `awaiting_reply` handles each criterion fill, advancing one at a time; when all criteria are filled, emits `ready_to_create` with full `goal_payload`.
  - State machine branches: `match_confirm` (after "Use this goal type"), `rephrase` (clears draft), `edit` (from ready_to_create), `confirm_create` (reminder to tap "Create goal").
  - Added `POST /api/chat/sessions/{session_id}/create-goal` endpoint: 404 for nonexistent AND not-owned sessions (no existence leak via 403→404 conversion in the handler); 422 when session has not reached ready_to_create state; 422 for invalid payload validated via canonical `GoalCreate` schema; 201 with `goal_id` and `status` on success; session.goal_id linked and status set to `goal_created`.
  - Request schema: `CreateGoalRequest(goal_payload: dict)` receives raw payload; endpoint wraps flat criteria dict into `{criteria_type, criteria_data}` for `GoalCreate` validation. Response schema: `CreateGoalResponse(goal_id: uuid.UUID, status: str)`.
  - 61 backend chat tests pass green (31 test_chat_match, 20 test_chat_messages incl. 10 create-goal tests, 5 test_chat_sessions_api, 5 pre-existing chat tests in other files); full suite 389 passed, 13 pre-existing failures unrelated to chat.
- File list:
  - backend/app/routes/chat.py — `_process_turn`, `_classify_turn`, `_apply_reply_to_draft`, `_compute_missing_criteria`, `send_message`, `create_goal` endpoint
  - backend/app/schemas/chat.py — `CreateGoalRequest`, `CreateGoalResponse`
  - backend/tests/test_chat_messages.py — `_drive_to_ready_to_create` helper, 10 create-goal tests, updated `test_missing_criteria_advance_one_at_a_time`, `test_ready_to_create_payload_includes_all_required_fields`
  - backend/app/services/goal.py — `create_goal` shared service
  - stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md

## Senior Developer Review
- Review status: Pending
- Reviewer:
- Review notes:

## Review Follow-ups
- None.
