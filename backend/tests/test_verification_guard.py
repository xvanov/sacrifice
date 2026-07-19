"""Tests for verification guard: timeout enforcement and concurrency limits.

Covers:
- AC2.1: External verification path SHALL enforce an explicit timeout limit
- AC2.2: External verification path SHALL enforce an explicit concurrency limit
"""

import asyncio

import pytest
from app.core.verification_guard import (
    _get_verification_semaphore,
    run_with_verification_guard,
)

# ─── AC2.1: Timeout enforcement ───────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_enforcement_raises_timeout_error():
    """AC2.1: Verification guard raises TimeoutError when verify_fn exceeds timeout."""

    async def slow_verify(proof_data, criteria_data):
        await asyncio.sleep(10.0)
        return {"verification_status": "verified", "verification_details": {}}

    with pytest.raises(asyncio.TimeoutError):
        await run_with_verification_guard(
            slow_verify,
            {},
            {},
            timeout_seconds=0.05,
        )


@pytest.mark.asyncio
async def test_timeout_enforcement_allows_fast_verification():
    """AC2.1: Verification guard allows verify_fn that completes within timeout."""

    async def fast_verify(proof_data, criteria_data):
        return {"verification_status": "verified", "verification_details": {"ok": True}}

    result = await run_with_verification_guard(
        fast_verify,
        {"k": "v"},
        {"k2": "v2"},
        timeout_seconds=5.0,
    )
    assert result["verification_status"] == "verified"
    assert result["verification_details"]["ok"] is True


@pytest.mark.asyncio
async def test_timeout_enforcement_default_timeout_applies():
    """AC2.1: Verification guard applies default timeout when none is specified.

    The default (60s) is high enough that a fast function completes.
    """

    async def fast_verify(proof_data, criteria_data):
        return {"verification_status": "verified", "verification_details": {}}

    result = await run_with_verification_guard(fast_verify, {}, {})
    assert result["verification_status"] == "verified"


# ─── AC2.2: Concurrency limit enforcement ────────────────────────────


@pytest.mark.asyncio
async def test_concurrency_limit_saturates_semaphore():
    """AC2.2: Semaphore limits concurrent verification executions.

    Uses a small semaphore (2) and submits many tasks.  At most 2 can run
    concurrently; the others wait.  We prove the cap works by showing that
    after saturation, additional tasks cannot enter immediately.
    """
    # Reset semaphore for isolated test
    import app.core.verification_guard as vg

    vg._verification_semaphore = asyncio.Semaphore(2)

    running = 0
    max_running = 0
    running_lock = asyncio.Lock()

    async def tracked_verify(proof_data, criteria_data):
        nonlocal running, max_running
        async with running_lock:
            running += 1
            max_running = max(max_running, running)
        await asyncio.sleep(0.1)
        async with running_lock:
            running -= 1
        return {"verification_status": "verified", "verification_details": {}}

    # Launch 5 concurrent verification calls
    tasks = [run_with_verification_guard(tracked_verify, {}, {}) for _ in range(5)]
    results = await asyncio.gather(*tasks)

    assert all(r["verification_status"] == "verified" for r in results)
    assert max_running == 2  # concurrency cap enforced


@pytest.mark.asyncio
async def test_concurrency_limit_default_is_positive():
    """AC2.2: Default semaphore permits at least one concurrent execution."""
    import app.core.verification_guard as vg

    vg._verification_semaphore = None
    sem = _get_verification_semaphore()
    assert sem._value >= 1
