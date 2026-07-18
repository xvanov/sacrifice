# CLI module

## Purpose
The CLI module gives a local terminal interface for logging in and calling the Sacrifice API with the same authenticated identity used by the app (`backend/cli/main.py`, `backend/cli/client.py`).

## Entry points and shape
- `backend/cli/main.py` defines the `sacrifice` Click commands, including login/logout and authenticated resource operations.
- `backend/cli/client.py` loads config, stores the access token and user payload, and injects the bearer into outgoing HTTP requests.

## Auth relevance
The CLI uses the backend's browser-based OAuth flow. After the user completes provider auth, the CLI exchanges the returned auth code for an access token and persists it for reuse on subsequent commands (`backend/cli/main.py`, `backend/cli/client.py`). Because the CLI uses the same bearer scheme as the app, any compromise of that stored token grants the same backend capabilities.

## Current constraints
- Config is stored in `~/.config/sacrifice/config.json` (`backend/cli/client.py`).
- Access tokens are stored there as plaintext JSON values today (`backend/cli/client.py`).
- The CLI defaults to `http://localhost:8000` unless `SACRIFICE_API_URL` overrides it (`backend/cli/client.py`).
