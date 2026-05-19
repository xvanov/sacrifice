We are building the Sacrifice app according to the PRD (PRD.md).
First read activity.md to see what was recently accomplished, then read PRD.md for the full requirements and task list.

## Environment Setup

Before starting backend work, check if a `.env` file exists in the repo root.
- If `.env` exists, read it and use the values for configuration.
- If `.env` does NOT exist, copy `.env.example` to `.env` and prompt the user to fill in the required values before proceeding.

## Servers Are Already Running

The orchestrator has already started the backend and frontend as long-running
processes, and they will auto-reload when you edit code. **Do NOT try to start
uvicorn or `expo start` yourself** — port 8000 and 8082 are already bound and
your attempt will fail with EADDRINUSE.

- Backend (FastAPI, `--reload`): http://localhost:8000  (logs at `/home/k/sacrifice/logs/backend.log`)
- Frontend (Expo web): http://localhost:8082  (logs at `/home/k/sacrifice/logs/frontend.log`)
- Celery worker: NOT running by default. If your task genuinely needs it,
  start it with `cd backend && nohup celery -A app.core.celery_app worker --loglevel=info > /home/k/sacrifice/logs/celery.log 2>&1 & disown`.

After editing backend code, just wait ~2s for uvicorn's reloader to pick it up
(check `tail -20 /home/k/sacrifice/logs/backend.log` to confirm). After editing
frontend code, Expo HMR handles it. Verify in the browser against the already-
running servers.

## Work on Tasks

Open PRD.md and find the single highest priority task where `"passes": false`.

Use **Test-Driven Development (TDD)** for every task:
1. Read the task's user story and acceptance criteria
2. Write tests that validate the acceptance criteria **BEFORE** writing implementation code
3. Run the tests (they should fail initially — red phase)
4. Implement the feature until all acceptance tests pass (green phase)
5. Run all existing tests to ensure nothing is broken

Work on exactly ONE task:
1. Write tests for the acceptance criteria
2. Implement the feature
3. Run any available checks:
   - `cd backend && python -m pytest -v` (backend tests)
   - `cd frontend && npx expo lint` (frontend lint)
   - `cd frontend && npx tsc --noEmit` (typecheck)

**Tests must be meaningful**: they should test real behavior, not trivial pass-throughs. A task is only done when all acceptance criteria are validated by passing tests.

## Verify in Browser

After implementing, use agent-browser to verify your work:

1. Open the local server URL:
   ```
   agent-browser open http://localhost:8000/docs
   ```
   (FastAPI auto-docs) or `http://localhost:8082` (Expo web)

2. Take a snapshot to see the page structure:
   ```
   agent-browser snapshot -i -c
   ```

3. Take a screenshot for visual verification:
   ```
   agent-browser screenshot screenshots/[task-name].png
   ```

4. Check for any console errors or layout issues

5. If the task involves interactive elements, test them:
   ```
   agent-browser click "[selector]"
   agent-browser fill "[selector]" "test value"
   ```

## Log Progress

Append a dated progress entry to activity.md describing:
- What you changed
- What commands you ran
- The screenshot filename
- Any issues encountered and how you resolved them

## Update Task Status

When the task is confirmed working, update that task's `"passes"` field in PRD.md from `false` to `true`.

## Commit Changes

Make one git commit for that task only with a clear, descriptive message:
```
git add .
git commit -m "feat: [brief description of what was implemented]"
```

Do NOT run `git init`, do NOT change git remotes, and do NOT push.

## Important Rules

- ONLY work on a SINGLE task per iteration
- Always verify in browser before marking a task as passing
- Always log your progress in activity.md
- Always commit after completing a task

## Completion

Before outputting `<promise>COMPLETE</promise>`, you MUST explicitly count:
1. Open PRD.md
2. Count how many tasks have `"passes": true` and how many have `"passes": false`
3. Only output COMPLETE if EVERY task has `"passes": true` and exactly ZERO tasks have `"passes": false`
4. If any tasks remain, do NOT output COMPLETE — just end the iteration normally

Output:
<promise>COMPLETE</promise>
