"""In-memory sliding-window rate limiter for public-facing API routes.

Protects unauthenticated auth/OAuth endpoints from brute-force and DoS abuse.
Uses a per-IP sliding window with configurable limit and window seconds.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request

# Per-IP request history: {ip: [timestamp, ...]}
_store: dict[str, list[float]] = defaultdict(list)

# Clean up stale entries periodically so _store doesn't grow unbounded.
# Counters reset after each window; this limits size to ~active IPs × max burst.
_MAX_STORED_IPS = 10_000
_CLEAN_COUNTDOWN = 1_000


def _clean_stale_entries(now: float) -> None:
    """Periodically prune IPs whose most recent entry is stale."""
    _store["__clean_counter"] = [now]
    count = _store["__clean_counter"]
    if len(count) < _CLEAN_COUNTDOWN:
        return
    count.clear()
    stale_ips = [
        ip
        for ip, timestamps in _store.items()
        if not ip.startswith("__") and (not timestamps or now - timestamps[-1] > 3600)
    ]
    for ip in stale_ips:
        del _store[ip]
    # If still too many, prune oldest-accessed keys
    if len(_store) > _MAX_STORED_IPS:
        sorted_ips = sorted(
            (ip for ip in _store if not ip.startswith("__")),
            key=lambda ip: _store[ip][-1] if _store[ip] else 0,
        )
        for ip in sorted_ips[: len(sorted_ips) - _MAX_STORED_IPS]:
            del _store[ip]


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request, checking proxy headers first."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    if client and client.host:
        return client.host
    return "127.0.0.1"


class RateLimitExceeded(Exception):
    """The client has exceeded the allowed request rate."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded, retry after {retry_after:.1f}s")


async def check_rate_limit(
    request: Request,
    max_requests: int = 10,
    window_seconds: float = 60.0,
) -> None:
    """Check whether ``request``'s client IP has exceeded the rate limit.

    Args:
        request: The incoming FastAPI request.
        max_requests: Maximum allowed requests per window.
        window_seconds: Sliding window duration in seconds.

    Raises:
        RateLimitExceeded: If the limit is exceeded, with ``retry_after`` hint.
    """
    ip = _get_client_ip(request)
    now = time.monotonic()

    timestamps = _store[ip]

    # Remove expired entries outside the window
    cutoff = now - window_seconds
    while timestamps and timestamps[0] < cutoff:
        timestamps.pop(0)

    if len(timestamps) >= max_requests:
        oldest = timestamps[0]
        retry_after = window_seconds - (now - oldest)
        raise RateLimitExceeded(retry_after=max(0.0, retry_after))

    timestamps.append(now)

    # Periodic cleanup
    _clean_stale_entries(now)