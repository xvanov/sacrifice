# Sacrifice PRD Snapshot

## Product identity
Sacrifice is an accountability product where a user creates a goal, attaches a monetary pledge, chooses a charity, submits proof before a deadline, and risks having the pledge charged and donated if verification fails or the deadline passes. This summary is distilled from the repository's existing `PRD.md` and is intended to be the lowercase canonical counterpart.

## Primary users
- **The Hustler**: an individual contributor using money-at-risk accountability to finish work.
- **The Builder**: a developer who wants programmable verification for technical deliverables.
- **The Charity**: a passive beneficiary that receives donations when users fail goals.

## Core product flow
1. User signs in.
2. User creates a goal with title, description, deadline, pledge amount, verification type, and charity.
3. User submits proof before the deadline.
4. The system verifies that proof.
5. If proof is accepted, the goal completes and no charge is made.
6. If proof is missing or rejected, the pledge is charged and sent to the chosen charity.

## Verification modes named in the original PRD
- **YouTube Video**: fetch metadata/transcript and judge whether the video matches the promised goal.
- **API Endpoint**: call an endpoint and validate the response against the expected spec.
- **Dev Sandbox**: clone code into a disposable environment, run tests, and use an LLM-backed review step.

## Current code-visible extension
The current codebase also defines a fourth goal type, `github_repo`, in `frontend/types/index.ts`, `backend/app/routes/goals.py`, and `backend/cli/main.py`.

## Current implementation snapshot from the activity log
`activity.md` records completed work for the backend app scaffold, database models and migrations, Expo frontend setup, OAuth and email login, goal CRUD, goal creation UI, goal list/detail screens, proof submission flows, dashboard endpoints and UI, payment-method and charity endpoints, and the notification feed.
