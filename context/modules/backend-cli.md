# backend-cli

## What this module is
`backend/cli/` packages a local command-line client for the same backend used by the frontend. It is installed as the `sacrifice` console script (`backend/pyproject.toml`, `backend/cli/main.py`).

## Entry points read
- `backend/cli/main.py`
- `backend/cli/client.py` (interface extracted via class and method names)

## Public shape
The CLI is a Click command group with an optional `--api-url` override and command families for login, whoami/logout, goals, dashboard, and notifications (`backend/cli/main.py`).

Visible behaviors from `main.py` include:
- Browser-based OAuth login through `/api/auth/cli/login/{provider}?port=...`
- A `dev-token` command for local JWT issuance when backend debug mode is enabled
- Goal creation flows that include at least dev-sandbox and GitHub repo goal helpers in the file portion read
- Dashboard commands for stats and history
- Notification commands for listing, unread count, read, and read-all

The `APIClient` wrapper in `client.py` exposes methods for login, whoami, list/get/create/update/delete goals, submit proof, read verification status, fetch dashboard data, and manage notifications (`backend/cli/client.py`).

## Notable current behaviors
- The CLI persists and clears auth state locally through helper functions imported from `cli.client` (`backend/cli/main.py`).
- Goal formatting assumes pledge amounts are stored in cents and displayed as currency values (`backend/cli/main.py`).
- The CLI shares the same backend contract as the frontend rather than using a separate service surface (`backend/cli/client.py`).

## Integration edges
- Talks directly to the FastAPI backend over HTTP.
- Depends on the backend OAuth and JWT flows.
- Provides a second user surface for creating and inspecting goals without the Expo app.

## Change guidance
Read this module before changing endpoint payloads or auth flows that must stay usable from the command line. If an API change would break parity with the frontend, update the CLI client at the same time.
