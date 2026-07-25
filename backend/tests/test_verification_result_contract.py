"""The shape of the outcome contract, independent of the database.

These tests are about how easy the contract is to misuse. Every one of them
pins a way of getting it wrong that must raise *before* anything is written,
because the fallback for a malformed outcome used to be ``failed`` — which
charges a card.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services import verification_result as vr

CHARGE_BOUNDARY = "app.workers.payments.process_charge_for_goal"


class _ExplodingSession:
    """Any database access at all is a contract-validation failure.

    Validation must reject bad input before it touches a row, so the safest
    assertion is that no session method was reachable.
    """

    async def execute(self, *a, **kw):  # pragma: no cover - must not be called
        raise AssertionError("validation must reject before any query")

    async def commit(self):  # pragma: no cover - must not be called
        raise AssertionError("validation must reject before any commit")


async def _call(status, details=None, **kw):
    with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
        await vr.persist_verification_result(
            _ExplodingSession(),
            uuid.uuid4(),
            uuid.uuid4(),
            status,
            details if details is not None else {},
            **kw,
        )
    return charge


# ─── Reason codes are a closed set ─────────────────────────────────────────


async def test_inconclusive_without_a_reason_is_rejected():
    """The bare word is not enough. Naming the fault is mandatory."""
    with pytest.raises(vr.InconclusiveContractError):
        await _call(vr.INCONCLUSIVE)


@pytest.mark.parametrize(
    "reason",
    [
        "user_repo_403",
        "repo_not_found",
        "tests_failed",
        "UPSTREAM_UNAVAILABLE",  # right code, wrong case
        "",
    ],
)
async def test_invented_reason_codes_are_rejected(reason):
    """No spelling of a user-fault cause is admissible, and codes are exact.

    This is the loophole guard: an ad-hoc reason string must not be a way to
    declare "not the user's fault" about something that plainly is.
    """
    with pytest.raises(vr.InconclusiveContractError):
        await _call(vr.INCONCLUSIVE, inconclusive_reason=reason)


async def test_unknown_status_is_rejected_rather_than_treated_as_failed():
    """A typo'd or new outcome must not degrade into the billing path."""
    for status in ("inconclusiv", "unknown", "error", "pending", "cancelled"):
        with pytest.raises(vr.InconclusiveContractError):
            await _call(status)


async def test_a_verdict_cannot_carry_a_reason_code():
    """No hedging in the other direction either.

    ``failed`` plus a reason code is a caller that has not decided whose fault
    it was; accepting it would mean the same payload could be read either way.
    """
    for status in (vr.VERIFIED, vr.FAILED):
        with pytest.raises(vr.InconclusiveContractError):
            await _call(status, inconclusive_reason=vr.REASON_UPSTREAM_UNAVAILABLE)


async def test_inconclusive_cannot_also_blame_the_user():
    """``failure_reason`` means "you failed" and cannot coexist with inconclusive.

    A run that measured a criterion and found it missed is a failure, whatever
    else went wrong alongside it. Rejecting the combination stops a verifier
    from quietly converting a real failure into a non-charging outcome by
    attaching an infrastructure code to it.
    """
    with pytest.raises(vr.InconclusiveContractError) as excinfo:
        await _call(
            vr.INCONCLUSIVE,
            {"failure_reason": "Only 1 of 3 required commits were found"},
            inconclusive_reason=vr.REASON_UPSTREAM_UNAVAILABLE,
        )
    # The message has to point at the sanctioned alternative, or the next
    # person to hit it will reach for the reason code that does not raise.
    assert "inconclusive_detail" in str(excinfo.value)


async def test_empty_failure_reason_is_not_treated_as_blame():
    """A falsy ``failure_reason`` is absence, not an accusation — do not reject.

    Verifiers initialise the details dict before knowing the outcome, so a
    ``None``/empty value is common and must not make the safe outcome
    unreachable.
    """
    for value in (None, "", []):
        try:
            await _call(
                vr.INCONCLUSIVE,
                {"failure_reason": value},
                inconclusive_reason=vr.REASON_UPSTREAM_UNAVAILABLE,
            )
        except vr.InconclusiveContractError as exc:
            pytest.fail(f"falsy failure_reason={value!r} rejected: {exc}")
        except AssertionError:
            # Reached the session, i.e. validation accepted it. That is the pass
            # condition — the exploding stub is how "got past validation" is
            # observed without a database.
            pass


# ─── The reason taxonomy itself ────────────────────────────────────────────


def test_reason_families_partition_the_allowlist():
    """Every code is exactly one of retryable or permanent.

    A code in neither set would be accepted by validation and then take the
    retryable branch by omission; a code in both would make the retry decision
    depend on set-iteration order.
    """
    assert vr.TRANSIENT_REASONS | vr.PERMANENT_REASONS == vr.INCONCLUSIVE_REASONS
    assert not (vr.TRANSIENT_REASONS & vr.PERMANENT_REASONS)
    assert vr.INCONCLUSIVE_REASONS


def test_no_user_fault_cause_is_in_the_allowlist():
    """The allowlist names components we run, not things a user controls.

    Written as a literal check on the accepted codes so that adding one is a
    deliberate act that has to be argued for in review: the failure mode this
    guards is a plausible-sounding code like ``repo_unavailable`` being added
    and silently making every 404 non-charging.
    """
    assert vr.INCONCLUSIVE_REASONS == frozenset(
        {
            "upstream_unavailable",
            "upstream_rate_limited",
            "sandbox_infrastructure",
            "criteria_not_evaluable",
            "internal_error",
        }
    )


def test_inconclusive_is_not_a_persistable_status():
    """It is an outcome, never a column value.

    ``proof_submissions.verification_status`` and ``goals.status`` are Postgres
    enums without it, so writing it through would be an InvalidTextRepresentation
    at commit time rather than a caught mistake.
    """
    assert vr.INCONCLUSIVE not in vr.VERDICTS
    assert vr.VERDICTS == frozenset({"verified", "failed"})
