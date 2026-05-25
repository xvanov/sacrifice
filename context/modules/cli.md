# CLI module

## Scope
This module is the Python command-line client in `backend/cli`. It wraps the same backend API used by the frontend and exposes local commands through the `sacrifice` console script declared in `backend/pyproject.toml`.

## Entry points
- `backend/cli/main.py` — Click command group, browser-based login flow, dev-token helper, and command formatting helpers.
- `backend/cli/client.py` — persistent config handling and the HTTP client wrapper around backend endpoints.

## Public surface
The CLI authenticates users, stores the resulting token locally, and then calls backend routes for:
- user identity checks
- goal list / get / create / update / delete
- proof submission and verification status
- dashboard stats and history
- notifications

## State and configuration
- The CLI reads and writes `~/.config/sacrifice/config.json` (`backend/cli/client.py`).
- The backend base URL comes from `SACRIFICE_API_URL`, persisted config, or the hardcoded default `http://localhost:8000` (`backend/cli/client.py`).
- Browser login uses a temporary localhost callback server and supports GitHub or Google providers (`backend/cli/main.py`).

## Current constraints
- CLI behavior depends on machine-local config state outside the repository (`backend/cli/client.py`).
- The CLI assumes the backend is already running and reachable; it does not start services itself (`backend/cli/client.py`, `Makefile`).
- A development shortcut exists through `sacrifice dev-token`, but it only works when backend debug mode is enabled (`backend/cli/main.py`, `backend/app/config.py`).
