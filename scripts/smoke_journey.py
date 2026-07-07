#!/usr/bin/env python3
"""Runtime smoke journey — the oracle the factory was missing.

Drives the exact path that shipped broken (the backlog was all-green while
the app could not log in or start a goal):

    register → login → create goal → activate → submit proof

against a LIVE backend. Pure stdlib (urllib) so it needs zero install on the
host — the only requirement is a running backend at ``SMOKE_BASE_URL``
(default http://localhost:8000), which ``scripts/smoke.sh`` boots via
docker compose.

The journey deliberately stops at proof-submission (HTTP 202 "accepted"):
that covers the entire authenticated write path without depending on the
async verifier (Celery) or the LLM, so the smoke is deterministic and
offline. Verification completion is a separate concern.

Exit 0 = the product runs. Exit 1 = a step the user hits is broken.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
# The URL the BACKEND uses to reach itself for api_endpoint verification. This
# differs from BASE when the backend runs in a container behind a remapped host
# port: the client (this script, on the host) talks to the mapped port, but the
# verifier (inside the container) must use the container-internal port. Defaults
# to BASE, which is correct for a host-run backend.
VERIFY_BASE = os.environ.get("SMOKE_VERIFY_URL", BASE).rstrip("/")
# Unique per run so repeated smokes never collide on "email already registered".
EMAIL = f"smoke+{int(time.time())}-{os.getpid()}@example.com"
PASSWORD = "SmokeTest123!"


def _req(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    expect: tuple[int, ...],
) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted localhost)
            status = resp.status
            raw = resp.read().decode() or "{}"
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode() or "{}"
    except urllib.error.URLError as e:
        _fail(f"{method} {path}", f"connection error: {e.reason}")
        raise  # unreachable; _fail exits
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"_raw": raw}
    if status not in expect:
        _fail(f"{method} {path}", f"expected {expect}, got {status}: {raw[:500]}")
    print(f"  ✓ {method} {path} → {status}")
    return parsed if isinstance(parsed, dict) else {"_list": parsed}


def _fail(step: str, msg: str) -> None:
    print(f"  ✗ {step}: {msg}", file=sys.stderr)
    print("SMOKE FAILED", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    print(f"Smoke journey against {BASE} as {EMAIL}")

    # 1. Register — the login-bug class lives here. A working token must come back.
    reg = _req(
        "POST",
        "/api/auth/email/register",
        body={"email": EMAIL, "password": PASSWORD, "display_name": "Smoke"},
        expect=(200, 201),
    )
    if not reg.get("access_token"):
        _fail("register", f"no access_token in response: {reg}")

    # 2. Login — confirm the credentials round-trip and mint a usable token.
    login = _req(
        "POST",
        "/api/auth/email/login",
        body={"email": EMAIL, "password": PASSWORD},
        expect=(200,),
    )
    token = login.get("access_token")
    if not token:
        _fail("login", f"no access_token in response: {login}")

    # 3. Create a goal (authenticated write) — "start a goal" path.
    # Use the `api_endpoint` goal type (a registered type whose verifier runs
    # server-side) and point its criteria at the backend's OWN /api/health, so
    # verification is offline and deterministic — the server checks itself,
    # no external network, no LLM, no Celery.
    self_url = f"{VERIFY_BASE}/api/health"
    deadline = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    goal = _req(
        "POST",
        "/api/goals",
        token=token,
        body={
            "title": "Smoke Test Goal",
            "goal_type": "api_endpoint",
            "pledge_amount": 5000,
            "currency": "usd",
            "deadline": deadline,
            "timezone": "UTC",
            "recurrence": "none",
            "criteria": {
                "url": self_url,
                "method": "GET",
                "expected_status": 200,
            },
        },
        expect=(200, 201),
    )
    goal_id = goal.get("id")
    if not goal_id:
        _fail("create goal", f"no id in response: {goal}")

    # 4. Activate (draft → active).
    _req(
        "PUT",
        f"/api/goals/{goal_id}",
        token=token,
        body={"status": "active"},
        expect=(200,),
    )

    # 5. Submit proof — accepted (202) is success; the async verifier is out of
    # scope. A "rejected" verdict returns 200, which is also a green smoke (the
    # submission path ran end-to-end); only a 4xx/5xx error fails the gate.
    _req(
        "POST",
        f"/api/goals/{goal_id}/submit-proof",
        token=token,
        body={"url": self_url, "method": "GET", "expected_status": 200},
        expect=(200, 202),
    )

    print("SMOKE PASSED — register → login → create → activate → submit-proof all green")


if __name__ == "__main__":
    main()
