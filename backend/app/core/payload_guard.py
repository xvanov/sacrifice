"""JSON payload size and depth guards for abuse-resistant endpoints.

Provides :func:`validate_json_payload` which checks a parsed JSON body against
configured size and nesting-depth limits, raising typed errors so routes can
return appropriate HTTP status codes.
"""

from __future__ import annotations

import json
from typing import Any


class PayloadTooLargeError(ValueError):
    """JSON payload exceeds the configured maximum serialized size."""


class PayloadTooDeepError(ValueError):
    """JSON payload exceeds the configured maximum nesting depth."""


# ── implementation-selected defaults ──────────────────────────────────────
# These are chosen as reasonable starting points for proof-submission payloads.
# The story (D057 / add-abuse-controls) does not prescribe concrete thresholds.
DEFAULT_MAX_SIZE_BYTES: int = 1_048_576   # 1 MiB
DEFAULT_MAX_DEPTH: int = 10


def _compute_depth(obj: Any, current_depth: int = 0) -> int:
    """Return the maximum nesting depth of *obj*.

    Scalars contribute ``current_depth + 1`` (the depth at which they sit).
    Dicts and lists recurse into their children.
    """
    next_depth = current_depth + 1
    if isinstance(obj, dict):
        if not obj:
            return next_depth
        return max(_compute_depth(v, next_depth) for v in obj.values())
    if isinstance(obj, list):
        if not obj:
            return next_depth
        return max(_compute_depth(item, next_depth) for item in obj)
    # scalar: str, int, float, bool, None
    return next_depth


def validate_json_payload(
    body: dict | list,
    *,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> None:
    """Validate *body* against size and nesting-depth limits.

    Args:
        body: The parsed JSON body (``dict`` or ``list``).
        max_size_bytes: Maximum serialized byte length (default 1 MiB).
        max_depth: Maximum nesting depth (default 10).

    Raises:
        PayloadTooLargeError: Serialized size exceeds *max_size_bytes*.
        PayloadTooDeepError: Nesting depth exceeds *max_depth*.
    """
    serialized = json.dumps(body, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > max_size_bytes:
        raise PayloadTooLargeError(
            f"Request body exceeds maximum size of {max_size_bytes} bytes"
        )

    depth = _compute_depth(body)
    if depth > max_depth:
        raise PayloadTooDeepError(
            f"Request body exceeds maximum nesting depth of {max_depth} "
            f"(actual depth: {depth})"
        )