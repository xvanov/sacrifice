# Sacrifice

Sacrifice is a goal-commitment app: a user creates a goal, puts money at risk, submits proof before a deadline, and either gets verified or gets charged if the goal is not verified. The running system combines a FastAPI backend, an Expo mobile/web client, and a local CLI so the same authenticated account can create goals, manage pledges, submit proof, and track outcomes.

## Local development quickstart

The repository root `Makefile` includes the main local development targets documented here: `make up`, `make down`, `make test`, and `make smoke`.

### 1. Prepare local configuration

If you do not already have a root `.env`, seed it from the checked-in example:

```bash
cp .env.example .env
```

Then install backend and frontend dependencies from the manifests in `backend/` and `frontend/`.

### 2. Start the stack

```bash
make up
```

This starts the local Postgres container, the FastAPI backend on port `8000`, the Expo web frontend on port `8082`, and a local `sacrifice` CLI symlink.

### 3. Run tests

```bash
make test
```

This runs backend `pytest` and frontend `jest`.

### 4. Run the smoke journey

```bash
make smoke
```

This runs the repository smoke path in `scripts/smoke.sh` to exercise the core product flow.

### 5. Stop the stack

```bash
make down
```

This stops the frontend, backend, optional Celery worker, and the local Postgres container.

## Architecture

- **Backend (`backend/`)**: FastAPI application with SQLAlchemy async models, Alembic migrations, PostgreSQL persistence, Redis-backed Celery support, and the `sacrifice` CLI (`backend/pyproject.toml`, `backend/app/main.py`, `backend/cli/`).
- **Frontend (`frontend/`)**: Expo SDK 54 / React Native app for web and mobile (`frontend/package.json`, `frontend/App.tsx`).
- **Local infrastructure (`docker-compose.yml`)**: development services for Postgres, Redis, and the backend container.

The backend currently serves health, auth, chat, dashboard, goal types, goals, notifications, payments, uploads, and webhook routes, and background work runs through Celery when a task needs it.

## Deeper docs

Current-state documentation lives under `context/`, especially:

- `context/project.md`
- `context/current-state.md`
- `context/navigation.md`
- `context/modules/`

## Software-factory relationship

Sacrifice also participates in the surrounding software-factory workflow. Factory directions and stories live under `software-factory/apps/sacrifice/`, and the development compose stack bind-mounts `software-factory/apps/sacrifice/directions` into the backend container. Repository CI runs through `.github/workflows/ci.yml`.
