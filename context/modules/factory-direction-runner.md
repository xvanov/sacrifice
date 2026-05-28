# factory-direction-runner

## What exists in-repo
The repository contains configuration and instructions for an external agent runner, not an application module that ships with Sacrifice.

- `PROMPT.md` defines the default execution workflow for repo tasks: read `activity.md`, then `PRD.md`, do not start backend or Expo because the orchestrator already runs them, and treat Celery as opt-in.
- `activity.md` records implementation progress and what has already been built.
- `opencode.json` configures the external runner's model, Azure provider wiring, skill path, and broad read/edit/bash permissions.

## What does not exist in the files read
There is no Python package, TypeScript package, FastAPI router, or Expo screen named `factory-direction-runner`. The current factory-style behavior is a repository boundary concern, not part of the shipped product runtime (`PROMPT.md`, `opencode.json`, `frontend/App.tsx`, `backend/app/main.py`).

## Why it matters for the generator direction
Generated goal types will be produced by external orchestration and then land in this repo's normal product surfaces:
- backend plugin packages under `backend/app/goal_types/`
- backend schemas/models/routes if the new type must be selectable or persisted
- frontend creation/proof flows if the user needs a new intake or proof experience

In other words, the runner may decide **what** to generate, but the app code still decides whether that output is discoverable, selectable, and verifiable.

## Operational constraints to keep in mind
- Ports `8000` and `8082` are already occupied by the orchestrator-managed backend and frontend according to `PROMPT.md`.
- Celery is not running by default according to `PROMPT.md`, which matters for any generated plugin that depends on background verification.
- The repo's automation config points at Azure Foundry-backed `opencode-go/deepseek-v4-flash`, which lines up with the backend settings for Azure Foundry credentials (`opencode.json`, `backend/app/config.py`).
