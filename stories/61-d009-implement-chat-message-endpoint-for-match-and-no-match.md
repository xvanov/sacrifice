# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record
- Status: Complete (reviewer retry-contract changes addressed)
- Agent model: openhands
- Debug log references: reviewer-fixes-61
- Completion notes:
  - Corrected the retryable matcher-failure contract so `POST /api/chat/sessions/{session_id}/messages` persists and returns the assistant retry message with `action: null` in the `502` body.
  - Kept the chat screen contract-compatible by deriving the retry affordance from HTTP `502` handling plus the returned message list, not from any non-spec `retry` action.
  - Updated backend coverage to assert the `502` response body and persisted assistant retry message both keep `action` null.
  - Updated frontend coverage to hydrate the server-returned `502` message body, render the persisted assistant retry text, and show the retry button from transport-level failure handling.
  - Verification run: `backend/tests/test_chat_messages.py` + `backend/tests/test_chat_match.py` passed (`45 passed`), and `frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx` passed (`12 passed`).
- File list:
  - backend/app/routes/chat.py
  - backend/app/services/chat_match.py
  - backend/tests/test_chat_messages.py
  - frontend/screens/ChatGoalCreateScreen.tsx
  - frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx
  - frontend/services/api.ts
  - stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md

## Senior Developer Review
- Review status: Pending
- Reviewer:
- Review notes:

## Review Follow-ups
- Broader repository suite reruns still fail for unrelated pre-existing issues outside this story slice. Current failures include `backend/e2e_test.py`, proof-submission contract tests, Alembic multiple-head/migration checks, registry smoke discovery, and `frontend/__tests__/screens/DevSandboxSubmissionScreen.test.tsx`. The D009-focused chat backend and frontend suites remain green.
