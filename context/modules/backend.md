# Backend module

## Purpose
The backend module runs the FastAPI API and is the enforcement point for authentication, goal lifecycle rules, payment actions, uploads, notifications, and webhook handling (`backend/app/main.py`).

## Entry points and shape
- `backend/app/main.py` builds the FastAPI app, configures CORS, discovers goal types at startup, and includes routers for auth, chat, dashboard, goals, notifications, payments, uploads, and webhooks.
- `backend/app/routes/goals.py` shows the standard protected-route pattern: routes depend on `get_current_user`, operate on the authenticated user's records, and expose goal-type metadata plus goal CRUD/proof flows.
- `backend/app/routes/payment.py` uses the same auth dependency for Stripe setup intents, payment methods, and payment history.

## Auth relevance
The backend treats bearer authentication as a shared primitive, not a separate edge concern. A token accepted by `get_current_user` becomes the authorization key for high-impact actions across goals and payments, which is why token replay and token storage quality matter so much in this app (`backend/app/core/dependencies.py`, `backend/app/routes/goals.py`, `backend/app/routes/payment.py`).

## Current constraints
- CORS is intentionally permissive for localhost, selected LAN/Tailscale IPs, and an ngrok host to support local Expo/device testing (`backend/app/main.py`).
- Goal-type discovery happens during startup, so misconfigured goal modules break boot deterministically instead of failing lazily on the first request (`backend/app/main.py`).
- The backend codebase includes an optional Celery/Redis path in the manifest, but the inspected auth flow runs in-process through FastAPI request handling (`backend/pyproject.toml`, `backend/app/main.py`).
