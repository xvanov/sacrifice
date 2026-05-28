# factory-directions

## What this slice is
This slice captures the repository documents that currently steer implementation work around Sacrifice: the product requirements in `PRD.md`, the task-runner instructions in `PROMPT.md`, the implementation log in `activity.md`, and the architecture summaries under `context/` (`PRD.md`, `PROMPT.md`, `activity.md`, `context/architecture-diagrams.md`).

## Current direction documents
- `PRD.md` describes Sacrifice as an accountability platform where users create goals, stake money, choose a verification type, and submit proof. In the scanned PRD, the MVP proof models are YouTube video, API endpoint, and dev sandbox verification (`PRD.md`).
- `PROMPT.md` instructs implementation agents to read `activity.md`, then `PRD.md`, work one task at a time, rely on the already running backend/frontend servers, and verify work before marking tasks complete (`PROMPT.md`).
- `activity.md` records delivered work such as goal CRUD, goal creation UI, dashboard, notifications, and payment integration. In the portion read for this scan, the log does not describe a shipped chat-factory goal creation path (`activity.md`).
- `context/architecture-diagrams.md` now summarizes the current implemented backend/frontend flow and the present D010 boundary where a chat-factory flow would need to attach (`context/architecture-diagrams.md`).

## Why it matters for D010
The repository's in-tree direction docs still describe a typed goal-creation product rather than an implemented chat generator. That means D010 architecture work should be explicit about the difference between current runtime behavior and proposed generator behavior, and should anchor any future design to the existing goal, proof, registry, and mobile constraints already documented elsewhere in `context/` (`PRD.md`, `PROMPT.md`, `context/current-state.md`).

## Files read
- `PRD.md`
- `PROMPT.md`
- `activity.md`
- `context/architecture-diagrams.md`
- `context/current-state.md`
