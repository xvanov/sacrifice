"""Verification guard: timeout and concurrency limits for external verifications.

Provides:
- asyncio.wait_for bound on verification calls
- asyncio.Semaphore concurrency cap
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

_DEFAULT_TIMEOUT_SECONDS = 60
_DEFAULT_MAX_CONCURRENT = 10

_verification_semaphore: asyncio.Semaphore | None = None


def _get_verification_semaphore(max_concurrent: int = _DEFAULT_MAX_CONCURRENT) -> asyncio.Semaphore:
    global _verification_semaphore
    if _verification_semaphore is None:
        _verification_semaphore = asyncio.Semaphore(max_concurrent)
    return _verification_semaphore


async def run_with_verification_guard(
    verify_fn: Callable[..., Coroutine[Any, Any, dict]],
    *args: Any,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> dict:
    """Run a verification function with timeout and concurrency limits.

    Args:
        verify_fn: Async verification coroutine function.
        *args: Positional arguments for verify_fn.
        timeout_seconds: Maximum time the verification may run.
        **kwargs: Keyword arguments for verify_fn.

    Returns:
        Verification result dict.

    Raises:
        asyncio.TimeoutError: If verification exceeds timeout_seconds.
    """
    semaphore = _get_verification_semaphore()
    async with semaphore:
        result = await asyncio.wait_for(
            verify_fn(*args, **kwargs),
            timeout=timeout_seconds,
        )
        return result
