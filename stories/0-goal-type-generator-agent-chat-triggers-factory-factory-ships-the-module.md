# Story 0 — Goal-type generator agent chat triggers factory, factory ships the module

## Status
- requested

## Why this story exists
The current codebase already has a backend goal-type registry that auto-discovers goal-type packages and routes proof verification through that registry, but the rest of the lifecycle is still fixed around four built-in types. `GoalCreate` validation, PostgreSQL enums, and the mobile creation form all hardcode `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`, while proof submission remains JSON-only and the inspected Expo config does not yet expose a camera plugin (`backend/app/goal_types/registry.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`, `frontend/app.json`).

This story asks the factory to close that gap by turning the existing registry seam into an end-to-end generator pipeline that can ship both regenerated built-in types and a novel camera-based `pushup_counter` type.

## Requested outcome
From agent chat, the factory should be able to generate a goal-type module, ship it into the app, and make it behave like a first-class goal type across creation, proof capture/submission, and verification.

## Acceptance criteria
1. **Regression**: the generator can regenerate one of the existing four goal types from a prompt that describes it. With the chat matcher artificially bypassed, the chain produces a module that passes the same fixtures that the existing module passes.
2. **Novel**: the canonical pushup case (D010's reason to exist) works end to end. From the prompt `Do 20 pushups every morning at 7am, verify with my phone camera`, the factory produces a `pushup_counter` module that uses D008's camera pipeline and passes fixture-based rep-counting assertions in CI.

## Current-state notes for implementers
- The most reusable seam today is `backend/app/goal_types/registry.py` plus the goal-type-agnostic verification call in `backend/app/routes/goals.py`.
- The highest-friction blockers are the fixed allowlists in backend schema/model code and the hardcoded goal-type UI in `frontend/screens/GoalCreateScreen.tsx`.
- Camera or uploaded-proof work is not yet wired into the current Expo plugin set or the JSON-only proof transport, so the pushup flow requires frontend and backend proof-pipeline expansion in addition to module generation.
