# Story

## Title
D009 add ChatGoalCreateScreen and route create-goal from home

## Dev Agent Record
- Status: Complete (chat goal-create shell replaces typed goal entrypoint)
- Agent model: openhands
- Debug log references:
  - `cd frontend && npx jest --runTestsByPath __tests__/screens/ChatGoalCreateScreen.test.tsx __tests__/screens/HomeScreen.test.tsx`
  - `cd frontend && npm run typecheck && npx jest --no-coverage`
- Completion notes:
  - Rebuilt `ChatGoalCreateScreen` as the active create-goal shell with session bootstrap, cached session resume, greeting/message rendering, structured action cards for `match_proposed`, `no_match`, and `awaiting_input`, and a send button disabled for empty or whitespace-only input.
  - Kept persistence local and defensive by reading/writing the stored chat session through web `localStorage` when available and Expo SecureStore otherwise, matching existing safe local-storage patterns.
  - Removed the legacy typed creation screen and its dedicated tests so create-goal flows now route through chat creation from home.
  - Strengthened frontend tests to cover bootstrap greeting persistence, stored-session resume, structured assistant affordances, and the simplified home navigation/UI expectations.
  - Installed `@playwright/test` as a local dev dependency so the existing strict TypeScript check can resolve Playwright imports already present in the frontend workspace.
- File list:
  - frontend/screens/ChatGoalCreateScreen.tsx
  - frontend/screens/GoalCreateScreen.tsx (deleted)
  - frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx
  - frontend/__tests__/screens/HomeScreen.test.tsx
  - frontend/__tests__/screens/GoalCreateScreen.test.tsx (deleted)
  - frontend/package.json
  - frontend/package-lock.json
  - stories/64-d009-add-chatgoalcreatescreen-and-route-create-goal-from-home.md

## Senior Developer Review
- Review status: Pending
- Reviewer:
- Review notes:

## Review Follow-ups
- None.
