# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record

### Status
Complete (reviewer change requests addressed)

### Completion Notes
Addressed all 4 reviewer change requests:

1. **[high]** `_compute_missing_criteria` now derives required top-level fields dynamically from `GoalCreate.model_fields` (checking for `PydanticUndefined` default and non-Optional annotation), plus goal-type-specific `criteria_schema.required` fields, so every field required by `POST /api/goals` is surfaced. `charity_id` is also included because the chat flow requires it.

2. **[high]** 502 retry path now persists the assistant message with `action: null` (a documented shape per `api_spec.md`) instead of the undocumented `action.type = "retry"`. The frontend can detect retry-ability from the HTTP 502 status code and message content.

3. **[test-quality 1]** `test_send_message_upstream_failure_returns_502` now asserts `messages[2]["action"] is None` per the documented contract instead of asserting `action.type == "retry"`.

4. **[test-quality 2]** `test_send_message_match_returns_200_with_match_proposed_action` now derives expected `missing_criteria` from the actual `GoalCreate.model_fields` (same logic as production `_compute_missing_criteria`) plus registry criteria requirements, instead of using a hardcoded set.

Additionally fixed `ChatSession` model to include `last_activity_at`, `goal_id`, `awaiting_direction_id`, and `session_id` columns present in the actual database schema.

All 20 chat tests pass (12 messages + 8 sessions). All 243 non-chat tests pass. 6 pre-existing unrelated failures remain unchanged.

### File List
- `backend/app/routes/chat.py`
- `backend/app/models/chat_session.py`
- `backend/tests/test_chat_messages.py`
- `stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md`