# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record
- Status: Complete (reviewer startup-retry and matched-path interaction fixes addressed)
- Agent model: openhands
- Debug log references: reviewer-fixes-61
- Completion notes:
  - `ChatGoalCreateScreen` now uses a reusable `initializeSession` path on mount and from the startup failure CTA, so a failed `createChatSession` request can be retried from the screen instead of leaving the user stranded.
  - The matched-path **Use this** flow remains server-backed: pressing the affordance posts `Use this goal type: <goal_type>` to `POST /api/chat/sessions/{session_id}/messages` and hydrates the assistant’s returned follow-up prompt.
  - Matched-turn draft extraction remains covered by the backend message-endpoint tests, which verify the response and persisted `draft_goal` include extracted partial fields such as title, pledge amount, deadline, and criteria data when parseable from the user prompt.
  - Frontend chat-screen tests were strengthened so the rendered chat is asserted directly, the named match-card test now presses **Use this** and checks the follow-up request plus `awaiting_input` render, and startup failure recovery is covered with a retry test.
  - Verification runs: `pytest -q tests/test_chat_messages.py` passed (`10 passed`); `npm test -- --runInBand --no-coverage __tests__/screens/ChatGoalCreateScreen.test.tsx __tests__/screens/HomeScreen.test.tsx` passed (`30 passed`).
- File list:
  - backend/app/routes/chat.py
  - backend/tests/conftest.py
  - backend/tests/test_chat_messages.py
  - frontend/App.tsx
  - frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx
  - frontend/__tests__/screens/HomeScreen.test.tsx
  - frontend/hooks/useNavigation.tsx
  - frontend/screens/ChatGoalCreateScreen.tsx
  - frontend/screens/HomeScreen.tsx
  - frontend/services/api.ts
  - stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md

## Senior Developer Review
- Review status: Pending
- Reviewer:
- Review notes:

## Review Follow-ups
- None.
