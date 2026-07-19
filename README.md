# Sacrifice

Sacrifice is a goal-commitment app: a user creates a goal, puts money at risk, submits proof before a deadline, and either gets verified or gets charged if the goal is not verified. The current product shape combines a FastAPI backend, an Expo mobile/web client, and a local CLI so users can create goals, manage pledges, submit proof, and track outcomes across the same authenticated system.

## Architecture

- **Backend (`backend/`)**: FastAPI API, SQLAlchemy async models, Alembic migrations, PostgreSQL persistence, Redis-backed Celery support, Stripe/payment integrations, OAuth flows, and the `sacrifice` CLI (`backend/pyproject.toml`, `backend/app/main.py`, `backend/cli/`).
- **Frontend (`frontend/`)**: Expo SDK 54 / React Native app for web and mobile (`frontend/package.json`, `frontend/App.tsx`).
- **Local infra (`docker-compose.yml`)**: development services for Postgres, Redis, and the backend container.
- **Docs (`context/`)**: current-state project docs, architecture notes, navigation guides, glossary, and module summaries for later contributors.

At runtime, the backend mounts routes for health, auth, chat, dashboard, goal types, goals, notifications, payments, uploads, and webhooks (`backend/app/main.py`). Background work is supported through Celery/Redis and is started separately when a task actually needs it (`Makefile`, `backend/pyproject.toml`).

## Local development quickstart

The repo root contains a `Makefile` with the main development entry points. The README only documents targets that are present today: `make up`, `make down`, `make test`, and `make smoke`.

### 1. Prepare local configuration

If you do not already have a root `.env`, seed it from the checked-in example:

```bash
cp .env.example .env
```

Then make sure backend and frontend dependencies are installed locally from the manifests in `backend/` and `frontend/`.

### 2. Start the app stack

From the repository root:

```bash
make up
```

This target starts:

- PostgreSQL
- the FastAPI backend on port `8000`
- the Expo web frontend on port `8082`
- a local `sacrifice` CLI symlink via `make cli-link`

### 3. Run tests

```bash
make test
```

This runs backend `pytest` and frontend `jest` from the existing Makefile target.

### 4. Run the smoke journey

```bash
make smoke
```

This runs the repository's fast runtime smoke path (`scripts/smoke.sh`) to exercise the core product flow.

### 5. Stop the stack

```bash
make down
```

This stops the frontend, backend, optional Celery worker, and the local Postgres container.

### Optional: start background jobs when needed

If your task needs background processing, the Makefile also includes a Celery target:

```bash
make celery
```

Stop it with:

```bash
make stop-celery
```

> Note: in software-factory managed sessions, `PROMPT.md` says the orchestrator may already own ports `8000` and `8082`, so contributors should avoid manually starting duplicate `uvicorn` or `expo start` processes there.

## Where to read next

The deeper current-state documentation lives under `context/`:

- `context/project.md` — project identity, stack, and top-level layout
- `context/current-state.md` — active architecture and current constraints
- `context/navigation.md` — which context files to read for specific tasks
- `context/modules/` — module-by-module summaries

## Software-factory relationship

Sacrifice also participates in the surrounding software-factory workflow. In that setup, the factory-managed app workspace lives at `software-factory/apps/sacrifice/`; the development compose file bind-mounts `software-factory/apps/sacrifice/directions` into the backend container (`docker-compose.yml`), and the companion factory `stories/` and `directions/` directories exist there. Repository CI runs through `.github/workflows/ci.yml`.
