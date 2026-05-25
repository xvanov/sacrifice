# CLI

## Purpose
The CLI is a packaged command-line client that authenticates against the same backend API and exposes goal, dashboard, and notification workflows without using the Expo frontend.

## Entry point
- `backend/cli/main.py` defines the Click command tree and browser-assisted OAuth login flow.

## Shape
- `backend/cli/main.py` holds command definitions and output formatting.
- `backend/cli/client.py` wraps HTTP requests, stores the access token and user info, and resolves the base URL.
- `backend/pyproject.toml` registers the `sacrifice` console script.

## Important behavior
- The CLI can open a browser and listen on a temporary localhost callback port during login (`backend/cli/main.py`).
- Stored credentials live in `~/.config/sacrifice/config.json` (`backend/cli/client.py`).
- CLI commands use the same `/api/auth`, `/api/goals`, `/api/dashboard`, and `/api/notifications` endpoints as other clients.
- A dedicated `dev-token` command exists for debug-mode backend sessions (`backend/cli/main.py`).

## Read next
- `backend/cli/main.py`
- `backend/cli/client.py`
- `backend/app/routes/auth.py`
