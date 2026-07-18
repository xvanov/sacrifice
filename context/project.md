# Sacrifice

## Identity
Sacrifice is an accountability app. A user creates a goal, puts money at risk, submits proof before a deadline, and can be charged if the goal is not verified. The product framing comes from `PRD.md`, while the running system today is a FastAPI backend, an Expo mobile/web client, and a small local CLI (`backend/app/main.py`, `frontend/App.tsx`, `backend/cli/main.py`).

This worktree is currently focused on auth hardening. The code already treats bearer-token compromise as high impact because the same authenticated session reaches goals, payments, notifications, uploads, dashboard data, and chat-adjacent flows through shared auth dependencies (`backend/app/core/dependencies.py`, `backend/app/routes/goals.py`, `backend/app/routes/payment.py`).

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy asyncio, Alembic, PostgreSQL via `asyncpg`, optional Celery/Redis worker path, Click CLI (`backend/pyproject.toml`, `backend/app/main.py`, `backend/cli/main.py`).
- Frontend: Expo SDK 54, React 19, React Native 0.81, TypeScript, NativeWind (`frontend/package.json`, `frontend/App.tsx`).
- Auth/security primitives: Google OAuth, GitHub OAuth, email/password auth, JWT bearer tokens, Fernet encryption for sensitive stored tokens (`backend/app/routes/auth.py`, `backend/app/services/auth.py`, `backend/app/core/crypto.py`).
- Integrations already configured in settings: Stripe, Redis, PostgreSQL, Google, GitHub, YouTube, Azure Foundry (`backend/app/config.py`).

## Top-level layout
- `backend/` — FastAPI app, auth routes/services, goal/payment/upload/notification APIs, models, tests, and the `sacrifice` CLI.
- `frontend/` — Expo app shell, auth/navigation hooks, screen components, API helpers, and frontend tests.
- `scripts/migration/` — bundle/bootstrap scripts for moving environment, database, and local state between machines.
- `context/` — canonical current-state docs for later agents.
- `e2e/`, `docker-compose.yml`, `.env` — local orchestration and end-to-end support.

## Active constraints
- Follow repo guidance in `PROMPT.md`: read `activity.md` before `PRD.md`, do not manually start uvicorn or Expo because the orchestrator already owns ports `8000` and `8082`, and only involve Celery when a task truly needs it.
- Frontend work should follow the Expo v54 documentation line called out by `frontend/AGENTS.md` and `frontend/package.json`.
- OAuth browser/mobile flows do not redirect raw access tokens back to the frontend. They redirect with a one-time `auth_code`, which the client exchanges server-side for the bearer token (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).
- Native and web clients persist the bearer locally, while the CLI persists it in `~/.config/sacrifice/config.json`; that makes token handling a first-order security concern (`frontend/services/auth.ts`, `backend/cli/client.py`).
- The current auth surface still has open hardening gaps noted in code/tests: no email verification, no password reset flow, and no visible rate limiting on login/register endpoints (`backend/tests/test_email_auth.py`, `backend/app/routes/auth.py`).

<!-- factory:context-refresh ts=2026-07-18T07:59:26.240512+00:00 after_pr=#224 -->
