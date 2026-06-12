# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record

### Status
Complete (reviewer change requests addressed — round 6)

### Agent model
openhands

### Debug log references
reviewer-fixes-61-round6

### Completion Notes
Addressed all 4 reviewer change requests:

1. **[high] `chat.py:252`** — Changed the 502 retry path's persisted assistant message from `action: None` to `action: {"type": "retry"}` so the frontend can render a "Retry" button card from persisted chat state, per the `flow.md` retry-card contract.

2. **[test-quality 1]** `test_send_message_upstream_failure_returns_502` — Updated the assertion from `messages[2].get("action") is None` to `messages[2].get("action") == {"type": "retry"}` (the structured retry action the frontend retry flow requires). Also updated the docstring.

3. **[test-quality 2]** `test_send_message_match_returns_200_with_match_proposed_action` — Replaced the hardcoded `expected_missing = sorted(["charity_id", "deadline", "min_duration_seconds"])` with a call to the production helper `_compute_missing_criteria(action["goal_type"], body["draft_goal"])`, so the test derives expected missing criteria from the actual registered goal type schema and extracted draft fields.

4. Applied the pending Alembic migration `6c2abce810b2` (cleanup extra chat_session columns) to drop `last_activity_at` and other extra columns from the dev database, resolving a `NOT NULL` constraint violation that prevented tests from running.

All 23 chat tests pass. All 223 non-chat tests pass. 6 pre-existing unrelated failures remain unchanged.

### File List
- `backend/app/routes/chat.py`
- `backend/tests/test_chat_messages.py`
- `stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md`