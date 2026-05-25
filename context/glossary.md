# Glossary

## Sacrifice
The product pattern at the center of this repo: a user commits to a goal and risks losing money if they do not complete it. The framing comes directly from the product requirements in `PRD.md`.

## Goal
The core unit a user creates. In the code paths read, a goal carries a title, description, goal type, deadline, pledge amount, currency, timezone, recurrence, and optional charity selection (`backend/app/routes/goals.py`, `backend/cli/main.py`).

## Pledge
The amount of money tied to failure. The CLI formats pledge amounts from integer cents, and the payment worker computes transfer amounts after fees (`backend/cli/main.py`, `backend/app/workers/payments.py`).

## Proof submission
The artifact a user submits to show that a goal was completed. Current clients and routes expose proof submission for YouTube videos, API endpoints, dev sandboxes, and GitHub repos (`backend/app/routes/goals.py`, `frontend/services/api.ts`).

## Goal type
The verification family attached to a goal. Current code paths explicitly reference `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/routes/goals.py`, `frontend/services/api.ts`).

## Charity
The destination organization for a failed pledge. The product document describes charity selection, and the backend activity log records a Stripe Connect-backed charity search API (`PRD.md`, `activity.md`).

## Recurrence
The schedule that causes a goal to spawn the next instance after its current deadline. The deadline worker handles `daily`, `weekly`, and `monthly` recurrence (`backend/app/workers/deadline.py`).

## Verification status
The result surface read by clients after async proof checking. The backend exposes `GET /api/goals/{goal_id}/verification-status`, and the frontend wraps that endpoint in `getVerificationStatus()` (`backend/app/routes/goals.py`, `frontend/services/api.ts`).

## Notification feed
The in-app stream of goal lifecycle events such as goal creation, proof receipt, and status changes. The activity log documents the current notification endpoints and UI wiring (`activity.md`).
