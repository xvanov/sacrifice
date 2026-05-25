# Backend module

## Scope
This module is the FastAPI application under `backend/app`. It owns the authenticated REST API for goals, dashboard data, notifications, payments, and health checks, and it is the coordination point for background verification work (`backend/app/main.py`).

## Entry points
- `backend/app/main.py` — creates the FastAPI app, configures CORS, and mounts the route groups.
- `backend/app/routes/goals.py` — the highest-signal route file for goal CRUD, proof submission, and verification-status polling.
- `backend/app/routes/auth.py` — OAuth and session entry points for web, CLI, and mobile flows.
- `backend/app/routes/payment.py` — Stripe setup-intent, payment-method, payments, and charity-search endpoints.

## Public surface
The backend currently exposes route families for:
- `/api/health`
- `/api/auth/*`
- `/api/dashboard/*`
- `/api/goals/*`
- `/api/notifications/*`
- `/api/payment/*`
- `/api/payments`
- `/api/charities/search`

The goal subsystem accepts four `goal_type` values: `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/models/goal.py`, `backend/app/schemas/goal.py`). The proof-submission route dispatches to goal-type-specific worker tasks via `.delay()` calls (`backend/app/routes/goals.py`).

## Data and integrations
- PostgreSQL through SQLAlchemy async sessions (`backend/app/database.py`).
- Redis / Celery for background verification (`backend/app/config.py`, `backend/app/routes/goals.py`).
- Stripe for setup intents, payment methods, payment history, and charity lookup (`backend/app/routes/payment.py`).
- Google and GitHub OAuth settings plus custom redirect handling (`backend/app/config.py`, `backend/app/routes/auth.py`).
- YouTube and Azure Foundry settings for verification workers (`backend/app/config.py`).

## Current constraints
- Environment comes from `../.env`, so backend behavior is tightly coupled to the repo-level environment file (`backend/app/config.py`).
- The default database and Redis URLs are local-development values, not production-safe defaults (`backend/app/config.py`).
- CORS origins are hardcoded rather than derived from environment (`backend/app/main.py`).
- Some payment and charity operations fail fast when Stripe is unconfigured (`backend/app/routes/payment.py`).
