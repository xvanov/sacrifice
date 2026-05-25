# Navigation

## When working on overall repository shape
- `context/project.md` — slow-changing identity, stack, and top-level layout
- `context/current-state.md` — current architecture and module map
- `context/architecture-diagrams.md` — current system flow and primary interaction path

## When working on the backend API
- `context/current-state.md` — router composition, storage, and integration constraints
- `context/modules/backend-app.md` — FastAPI entrypoint, settings, database, and goal-facing interfaces
- `context/glossary.md` — domain terms used across goals, pledges, proof, and charities

## When working on background verification, deadlines, or payments
- `context/current-state.md` — queueing model and current constraints
- `context/modules/backend-workers.md` — Celery includes, beat schedule, recurrence, and payment/disbursement notes
- `context/architecture-diagrams.md` — worker placement in the current system

## When working on the CLI
- `context/project.md` — where the CLI fits in the product
- `context/modules/backend-cli.md` — command groups, auth flow, and API client shape
- `context/current-state.md` — shared constraints with the backend API

## When working on the Expo client
- `context/project.md` — frontend stack and repo placement
- `context/modules/frontend.md` — screen switching, API client behavior, and frontend-specific constraints
- `context/glossary.md` — user-facing domain terms reflected in screens and actions

## When working on goals, proof submission, or verification status
- `context/current-state.md` — currently implemented proof paths and async model
- `context/modules/backend-app.md` — goal endpoints and proof dispatch
- `context/modules/backend-workers.md` — verification execution and deadline handling
- `context/modules/frontend.md` — frontend API methods and current screens

## When working on notifications or dashboard behavior
- `context/current-state.md` — feature coverage summary from the implementation log
- `context/modules/backend-app.md` — HTTP surfaces used by these features
- `context/modules/frontend.md` — client-side consumption of dashboard and notification APIs
