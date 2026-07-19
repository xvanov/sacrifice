"""Structured security logging for goal-type module loading and verification.

Emits JSON-formatted log events for:
- Module load decisions (allow/deny with reason)
- Verifier exception handling

All events omit proof payload contents and other sensitive detail.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger("sacrifice.security.goal_types")


def _emit(event: dict[str, Any]) -> None:
    """Serialize *event* as a single-line JSON record to stderr.

    Uses stderr so the event stream is separate from stdout application logs.
    A dedicated logger with a JSON formatter would be ideal, but avoiding
    additional dependencies keeps the implementation minimal per the story.
    """
    try:
        record = json.dumps(event, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        record = json.dumps(
            {"event_type": "security_log_error", "detail": "serialization_failed"},
            separators=(",", ":"),
        )
    # Flush-after-write so startup-failure logs are visible before a crash.
    print(record, file=sys.stderr, flush=True)
    # Also route through the standard logging hierarchy so pytest caplog
    # fixtures can capture events without scraping stderr.
    logger.info(record)


# ── Module load decision events ────────────────────────────────────────────────


def log_module_load_allow(
    module_name: str,
    *,
    trusted_path: str | None = None,
) -> None:
    """Record that *module_name* passed all trust checks and was loaded."""
    _emit(
        {
            "event_type": "goal_type_load_decision",
            "decision": "allow",
            "module_name": module_name,
            "trusted_path": trusted_path,
        }
    )


def log_module_load_deny(
    module_name: str,
    reason: str,
    *,
    detail: str | None = None,
) -> None:
    """Record that *module_name* was denied loading with the given *reason*."""
    event: dict[str, Any] = {
        "event_type": "goal_type_load_decision",
        "decision": "deny",
        "module_name": module_name,
        "reason": reason,
    }
    if detail:
        event["detail"] = detail
    _emit(event)


# ── Verifier exception events ──────────────────────────────────────────────────


def log_verifier_exception(
    goal_type: str,
    submission_id: str,
    exception_type: str,
    *,
    detail: str | None = None,
) -> None:
    """Record that a verifier dispatched for *goal_type* raised an exception.

    IMPORTANT: *detail* must NOT contain proof payload contents, credentials,
    or any other sensitive data. Callers are responsible for sanitising.
    """
    event: dict[str, Any] = {
        "event_type": "verifier_exception",
        "goal_type": goal_type,
        "submission_id": submission_id,
        "exception_type": exception_type,
    }
    if detail:
        event["detail"] = detail
    _emit(event)
