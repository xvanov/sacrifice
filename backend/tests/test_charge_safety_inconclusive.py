"""The charge boundary, tested from both directions.

``persist_verification_result`` is the only place a verification outcome can
reach ``process_charge_for_goal``, which creates a real off-session Stripe
PaymentIntent against the user's saved card. Two failures are possible here and
they are not symmetric in cost, so both are pinned:

* charging for OUR failure (a GitHub 502, an exhausted rate-limit quota, a
  Docker daemon restart, criteria nobody implemented) — bills an innocent user;
* NOT charging for the USER's failure — turns the product into a free pledge and
  hands anyone who can make verification error out a way to dodge payment.

Every test here asserts on the charge boundary itself, patched, rather than on
a proxy like the goal's status.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal, GoalCriteria
from app.models.proof import ProofSubmission
from app.models.user import User
from app.services import verification_result as vr

CHARGE_BOUNDARY = "app.workers.payments.process_charge_for_goal"


def _session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def _make_goal(
    db: AsyncSession,
    *,
    goal_status: str = "active",
    deadline_offset: timedelta = timedelta(days=3),
    submission_status: str = "pending",
    dispatch_attempts: int = 1,
    verification_details: dict | None = None,
    goal_type: str = "github_repo",
) -> tuple[Goal, ProofSubmission]:
    """A user + active goal + criteria + one submission, straight through the ORM.

    Built directly rather than through the API so the row can be put into states
    the API refuses to create (a spent attempt counter, a deadline in the past).
    """
    user = User(
        email=f"charge-safety-{uuid.uuid4()}@example.com",
        display_name="Charge Safety",
        auth_provider="google",
        auth_provider_id=str(uuid.uuid4()),
        stripe_customer_id="cus_test_dummy",
    )
    db.add(user)
    await db.flush()

    goal = Goal(
        user_id=user.id,
        title="Ship the thing",
        description="A goal under verification",
        goal_type=goal_type,
        pledge_amount=5000,
        currency="usd",
        deadline=datetime.now(timezone.utc) + deadline_offset,
        timezone="UTC",
        recurrence="none",
        status=goal_status,
        charity_id="acct_charity123",
    )
    db.add(goal)
    await db.flush()

    db.add(
        GoalCriteria(
            goal_id=goal.id,
            criteria_type=goal_type,
            criteria_data={"conditions": [{"type": "commit_count", "min_commits": 3}]},
        )
    )

    submission = ProofSubmission(
        goal_id=goal.id,
        submitted_at=datetime.now(timezone.utc),
        proof_data={"repo_url": "https://github.com/octocat/hello-world"},
        verification_status=submission_status,
        verification_details=verification_details,
        dispatched_at=datetime.now(timezone.utc),
        dispatch_attempts=dispatch_attempts,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(goal)
    await db.refresh(submission)
    return goal, submission


# ─── Our fault: must never charge ──────────────────────────────────────────
# One case per reason code, driven through the public entry point, because the
# guarantee has to hold for every admissible cause and not just the one that
# happened to be written first.


@pytest.mark.parametrize("reason", sorted(vr.INCONCLUSIVE_REASONS))
async def test_inconclusive_never_charges(reason):
    """Each infrastructure reason code reaches no charge and no verdict."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.INCONCLUSIVE,
                    {"repo_url": "https://github.com/octocat/hello-world"},
                    inconclusive_reason=reason,
                )
            charge.assert_not_awaited()

            await db.refresh(submission)
            await db.refresh(goal)
            # No verdict was reached, so neither row records one.
            assert submission.verification_status == "pending"
            assert goal.status == "active"
            assert submission.verification_details["outcome"] == vr.INCONCLUSIVE
            assert submission.verification_details["inconclusive_reason"] == reason
    finally:
        await engine.dispose()


async def test_transient_reason_stays_claimable_by_the_reconciler():
    """A GitHub blip must resolve itself: the row stays eligible for re-dispatch.

    Pinned against the reconciler's real claim predicates, not a restatement of
    them — if that query changes shape, this test is what notices.
    """
    from app.workers.reconcile_dispatch import count_stale_dispatches

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, dispatch_attempts=1)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.INCONCLUSIVE,
                    {},
                    inconclusive_reason=vr.REASON_UPSTREAM_UNAVAILABLE,
                )
            charge.assert_not_awaited()

            # Backdate past the staleness window: the reconciler only claims
            # rows old enough that no verification can still be in flight.
            stale = datetime.now(timezone.utc) - timedelta(
                minutes=settings.verification_dispatch_stale_minutes + 5
            )
            await db.execute(
                text(
                    "UPDATE proof_submissions SET submitted_at = :t, "
                    "dispatched_at = :t WHERE id = :id"
                ),
                {"t": stale, "id": submission.id},
            )
            await db.commit()

            assert await count_stale_dispatches(db) == 1
    finally:
        await engine.dispose()


async def test_permanent_reason_is_not_retried_and_lands_in_review():
    """Un-evaluatable criteria escalate now rather than burning the whole cap."""
    from app.workers.reconcile_dispatch import count_stale_dispatches

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, dispatch_attempts=1)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.INCONCLUSIVE,
                    {
                        "inconclusive_detail": "Unsupported condition type 'language_stats'"
                    },
                    inconclusive_reason=vr.REASON_CRITERIA_NOT_EVALUABLE,
                )
            charge.assert_not_awaited()

            await db.refresh(submission)
            assert submission.dispatch_attempts == (
                settings.verification_dispatch_max_attempts
            )
            assert submission.verification_details["inconclusive_retryable"] is False
            assert submission.verification_details["needs_operator_review"] is True

            stale = datetime.now(timezone.utc) - timedelta(
                minutes=settings.verification_dispatch_stale_minutes + 5
            )
            await db.execute(
                text(
                    "UPDATE proof_submissions SET submitted_at = :t, "
                    "dispatched_at = :t WHERE id = :id"
                ),
                {"t": stale, "id": submission.id},
            )
            await db.commit()
            # Cap already spent, so no retry is ever attempted.
            assert await count_stale_dispatches(db) == 0
    finally:
        await engine.dispose()


async def test_attempt_cap_boundary_escalates_without_charging():
    """The last permitted attempt flips to operator review — and still no charge.

    The cap boundary is the dangerous edge: it is where an automatic system runs
    out of ideas, and the wrong instinct there is to resolve the goal.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            cap = settings.verification_dispatch_max_attempts

            # One attempt left: still self-healing, no operator needed.
            goal, submission = await _make_goal(db, dispatch_attempts=cap - 1)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.INCONCLUSIVE,
                    {},
                    inconclusive_reason=vr.REASON_UPSTREAM_RATE_LIMITED,
                )
            charge.assert_not_awaited()
            await db.refresh(submission)
            assert submission.verification_details["needs_operator_review"] is False

            # Cap reached: escalated, still not charged, still not a verdict.
            goal2, submission2 = await _make_goal(db, dispatch_attempts=cap)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal2.id,
                    submission2.id,
                    vr.INCONCLUSIVE,
                    {},
                    inconclusive_reason=vr.REASON_UPSTREAM_RATE_LIMITED,
                )
            charge.assert_not_awaited()
            await db.refresh(submission2)
            await db.refresh(goal2)
            assert submission2.verification_details["needs_operator_review"] is True
            assert submission2.verification_status == "pending"
            assert goal2.status == "active"
            assert await vr.goal_verification_is_blocked(db, goal2.id) is True
    finally:
        await engine.dispose()


async def test_inconclusive_is_idempotent_under_replay():
    """The reconciler may replay this. Twice must equal once, and never charge."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, dispatch_attempts=1)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                for _ in range(3):
                    await vr.persist_verification_result(
                        db,
                        goal.id,
                        submission.id,
                        vr.INCONCLUSIVE,
                        {},
                        inconclusive_reason=vr.REASON_SANDBOX_INFRASTRUCTURE,
                    )
            charge.assert_not_awaited()

            await db.refresh(submission)
            await db.refresh(goal)
            assert submission.verification_status == "pending"
            assert goal.status == "active"
            # Replaying an outcome must not consume retry budget — only the
            # reconciler's own claim statement may move this counter.
            assert submission.dispatch_attempts == 1

            # And the owner is told once, not once per replay.
            count = await db.execute(
                text(
                    "SELECT COUNT(*) FROM notifications WHERE goal_id = :g "
                    "AND type = 'proof_received'"
                ),
                {"g": goal.id},
            )
            assert count.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_inconclusive_cannot_reopen_a_settled_submission():
    """A late replay must not undo a verdict a concurrent worker already wrote.

    Downgrading a resolved row back to ``pending`` would make it claimable
    again; on a ``failed`` row that is a path to a second charge.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            for settled in ("verified", "failed"):
                goal, submission = await _make_goal(
                    db,
                    goal_status=settled,
                    submission_status=settled,
                    verification_details={"condition_results": []},
                )
                with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                    await vr.persist_verification_result(
                        db,
                        goal.id,
                        submission.id,
                        vr.INCONCLUSIVE,
                        {},
                        inconclusive_reason=vr.REASON_UPSTREAM_UNAVAILABLE,
                    )
                charge.assert_not_awaited()
                await db.refresh(submission)
                await db.refresh(goal)
                assert submission.verification_status == settled
                assert goal.status == settled
                assert "outcome" not in (submission.verification_details or {})
    finally:
        await engine.dispose()


# ─── The user's fault: must still charge ───────────────────────────────────


async def test_genuine_user_failure_still_charges():
    """The existing failure path is untouched: verdict written, pledge charged."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db)
            details = {
                "repo_url": "https://github.com/octocat/hello-world",
                "failure_reason": "Only 1 of 3 required commits were found",
                "condition_results": [
                    {
                        "type": "commit_count",
                        "passed": False,
                        "failure_reason": "Only 1 of 3 required commits were found",
                    },
                ],
            }
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.FAILED,
                    details,
                )
            charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))

            await db.refresh(submission)
            await db.refresh(goal)
            assert submission.verification_status == "failed"
            assert goal.status == "failed"
            assert submission.verification_details == details
    finally:
        await engine.dispose()


async def test_mixed_outcome_confirmed_failure_outranks_inconclusive():
    """A rate limit alongside a real miss is still a real miss, and still charges.

    Criteria are conjunctive: "2 of 5 commits" is terminal on its own. If an
    inconclusive sibling check could suppress the charge, every pledge would be
    dodgeable by getting one check to error, so this is where the loophole would
    live.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db)
            details = {
                "failure_reason": "Only 2 of 5 required commits were found",
                "condition_results": [
                    {
                        "type": "commit_count",
                        "passed": False,
                        "failure_reason": "Only 2 of 5 required commits were found",
                    },
                    {
                        "type": "required_files",
                        "passed": False,
                        "inconclusive": True,
                        "error": "GitHub API error 429: rate limited",
                    },
                ],
            }
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.FAILED,
                    details,
                )
            charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))
            await db.refresh(goal)
            assert goal.status == "failed"
    finally:
        await engine.dispose()


async def test_mixed_outcome_cannot_be_relabelled_inconclusive():
    """The same mixed payload cannot be laundered into the non-charging path.

    The blame text is what makes it unlaunderable: a caller that has measured a
    criterion and found it missed cannot also claim the run was inconclusive.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                with pytest.raises(vr.InconclusiveContractError):
                    await vr.persist_verification_result(
                        db,
                        goal.id,
                        submission.id,
                        vr.INCONCLUSIVE,
                        {
                            "failure_reason": "Only 2 of 5 required commits were found",
                            "condition_results": [
                                {"type": "commit_count", "passed": False},
                                {
                                    "type": "required_files",
                                    "passed": False,
                                    "inconclusive": True,
                                },
                            ],
                        },
                        inconclusive_reason=vr.REASON_UPSTREAM_RATE_LIMITED,
                    )
            charge.assert_not_awaited()
            # Rejected before any write: the row is untouched, so the correct
            # FAILED call can still follow and charge.
            await db.refresh(submission)
            await db.refresh(goal)
            assert submission.verification_status == "pending"
            assert goal.status == "active"
    finally:
        await engine.dispose()


def test_verifier_aggregation_gives_confirmed_failure_precedence():
    """Cross-check of the upstream fold that decides which outcome arrives here.

    Owned by the github_repo workstream, not this one; asserted from this side
    because the guarantee only holds end-to-end if the aggregator agrees, and a
    regression there would silently convert real failures into free passes.
    """
    from app.workers.github_repo import verification_outcome

    confirmed = {"type": "commit_count", "passed": False}
    blipped = {
        "type": "required_files",
        "passed": False,
        "inconclusive": True,
        "inconclusive_reason": vr.REASON_UPSTREAM_RATE_LIMITED,
    }
    passing = {"type": "commit_count", "passed": True}

    # (status, reason) — a FAILED fold must carry no reason code, or the call
    # into persist_verification_result would be rejected as a hedge.
    assert verification_outcome([confirmed, blipped]) == (vr.FAILED, None)
    assert verification_outcome([blipped, confirmed]) == (vr.FAILED, None)
    assert verification_outcome([passing, blipped]) == (
        vr.INCONCLUSIVE,
        vr.REASON_UPSTREAM_RATE_LIMITED,
    )
    assert verification_outcome([passing]) == (vr.VERIFIED, None)


async def test_verified_result_is_unchanged_and_does_not_charge():
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.VERIFIED,
                    {"condition_results": [{"type": "commit_count", "passed": True}]},
                )
            charge.assert_not_awaited()
            await db.refresh(goal)
            assert goal.status == "verified"
    finally:
        await engine.dispose()


async def test_details_claiming_inconclusive_does_not_stop_the_charge():
    """The charge decision reads ``status`` alone — never the details payload.

    ``verification_details`` is assembled from user-supplied values and is
    echoed back through the verification-status endpoint. If it could veto a
    charge, the loophole would be whatever gets a verifier to copy an attacker's
    key into it.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.FAILED,
                    {
                        "outcome": vr.INCONCLUSIVE,
                        "inconclusive_reason": vr.REASON_UPSTREAM_UNAVAILABLE,
                        "needs_operator_review": True,
                        "failure_reason": "the test suite failed",
                    },
                )
            charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))
            await db.refresh(goal)
            assert goal.status == "failed"
    finally:
        await engine.dispose()


async def test_user_fault_verdict_is_not_blocked_for_the_deadline_sweep():
    """A user-failed goal is never reported as blocked-on-us."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.FAILED,
                    {"failure_reason": "missing required file README.md"},
                )
            assert await vr.goal_verification_is_blocked(db, goal.id) is False
    finally:
        await engine.dispose()


async def test_a_later_user_failure_clears_the_blocked_flag():
    """A blip followed by a real verdict is a real verdict.

    The blocked state must not be sticky: it is read from the goal's *latest*
    submission, so a user who retries and genuinely fails is charged normally.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, first = await _make_goal(db)
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    first.id,
                    vr.INCONCLUSIVE,
                    {},
                    inconclusive_reason=vr.REASON_UPSTREAM_UNAVAILABLE,
                )
            assert await vr.goal_verification_is_blocked(db, goal.id) is True

            second = ProofSubmission(
                goal_id=goal.id,
                submitted_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                proof_data={"repo_url": "https://github.com/octocat/hello-world"},
                verification_status="pending",
                dispatch_attempts=1,
            )
            db.add(second)
            await db.commit()
            await db.refresh(second)

            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    second.id,
                    vr.FAILED,
                    {"failure_reason": "the required tests do not pass"},
                )
            charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))
            assert await vr.goal_verification_is_blocked(db, goal.id) is False
    finally:
        await engine.dispose()


# ─── The deadline sweep: what the resulting state actually exposes ──────────


async def test_inconclusive_goal_is_not_selected_as_a_new_charge_candidate():
    """The state left behind adds nothing to the sweep's charge set.

    Asserted against ``check_deadlines``' real predicates. An inconclusive
    outcome leaves the goal in the status it already had (``active``) with its
    deadline untouched, so a goal with time left is not selectable — where
    ``pending_review`` (the shape one review proposed) would have made it
    selectable five minutes past the deadline.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, deadline_offset=timedelta(days=3))
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.INCONCLUSIVE,
                    {},
                    inconclusive_reason=vr.REASON_SANDBOX_INFRASTRUCTURE,
                )

            now = datetime.now(timezone.utc)
            grace = now - timedelta(minutes=5)
            selected = await db.execute(
                text(
                    """
                    SELECT id FROM goals
                    WHERE (status = 'active' AND deadline < :now)
                       OR (status = 'pending_review' AND deadline < :grace)
                    """
                ),
                {"now": now, "grace": grace},
            )
            assert goal.id not in {row[0] for row in selected}
    finally:
        await engine.dispose()


async def test_pending_review_is_swept_into_a_charge():
    """Why an inconclusive goal is NOT parked in ``pending_review``.

    ``pending_review`` was proposed as the safe parking status on the basis that
    the sweep charges only ``status='active'``. It does not: ``check_deadlines``
    runs a *second* query for ``pending_review`` goals past a five-minute grace
    (``app/workers/deadline.py``) and feeds them to the same charge call. This
    drives the real ``check_deadlines`` to show the charge actually happening,
    because the claim is load-bearing enough that reading the query is not
    enough.
    """
    from app.workers.deadline import check_deadlines

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, _ = await _make_goal(db, goal_status="pending_review")
            # Six minutes past the deadline: inside nothing, past the 5-minute
            # grace. Written directly because update_goal refuses a past deadline.
            await db.execute(
                text("UPDATE goals SET deadline = :d WHERE id = :id"),
                {
                    "d": datetime.now(timezone.utc) - timedelta(minutes=6),
                    "id": goal.id,
                },
            )
            await db.commit()
            goal_id, user_id = str(goal.id), str(goal.user_id)

        # check_deadlines opens its own session against settings.database_url.
        with patch(
            "app.workers.deadline.process_charge_for_goal", new_callable=AsyncMock
        ) as charge:
            summary = await check_deadlines()

        assert summary["processed_pending"] == 1
        charge.assert_awaited_once_with(goal_id, user_id)
    finally:
        await engine.dispose()


async def test_known_gap_expired_inconclusive_goal_is_still_swept():
    """Documents the one hole this module cannot close from inside itself.

    If the deadline passes while verification is blocked on us, the goal is
    still ``active`` and ``check_deadlines`` charges it — it never asks why
    there is no verdict. ``goal_verification_is_blocked`` is the predicate that
    closes it, and it must be called from ``app/workers/deadline.py``, which
    this workstream does not own.

    This test asserts the *current* behavior deliberately, so the gap is
    visible in CI instead of being a paragraph in a report. When the sweep
    starts consulting the predicate, this test will fail and should be inverted.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, deadline_offset=timedelta(days=3))
            with patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
                await vr.persist_verification_result(
                    db,
                    goal.id,
                    submission.id,
                    vr.INCONCLUSIVE,
                    {},
                    inconclusive_reason=vr.REASON_UPSTREAM_RATE_LIMITED,
                )
            # Deadline moved into the past directly: create/update_goal refuse to
            # do this, which is why the row is edited here.
            await db.execute(
                text("UPDATE goals SET deadline = :d WHERE id = :id"),
                {"d": datetime.now(timezone.utc) - timedelta(hours=1), "id": goal.id},
            )
            await db.commit()

            now = datetime.now(timezone.utc)
            selected = await db.execute(
                text(
                    "SELECT id FROM goals WHERE status = 'active' AND deadline < :now"
                ),
                {"now": now},
            )
            assert goal.id in {row[0] for row in selected}, (
                "expected the documented gap; if this fails the sweep changed"
            )
            # The signal the sweep needs is present and true.
            assert await vr.goal_verification_is_blocked(db, goal.id) is True
    finally:
        await engine.dispose()


# ─── The wire, not the endpoints ───────────────────────────────────────────
#
# Both halves of the api_endpoint path were already pinned and both were green
# while the path was broken: test_charge_integrity_conformance drives the
# PRODUCER (`verify_api_endpoint`) and asserts it returns a reason, and the
# tests above drive the PERSISTER (`persist_verification_result`) and assert a
# reason never charges. Nothing drove the join. `run_api_verification` dropped
# `result["inconclusive_reason"]` on the floor at both of its call sites, so the
# reason the producer computed never reached the persister the tests trusted.


@pytest.mark.parametrize(
    "reason",
    [vr.REASON_UPSTREAM_UNAVAILABLE, vr.REASON_INTERNAL_ERROR],
)
async def test_api_verification_wire_carries_the_reason_to_the_write(reason):
    """`run_api_verification` must persist our own fault, not raise on it.

    Consequence chain when the reason is dropped, all of it verified on main:
    `_validate` raises `InconclusiveContractError` BEFORE any write → the celery
    task retries three times and fails identically every time → the submission
    stays `pending` with NULL `verification_details` →
    `goal_verification_is_blocked` returns False because there is nothing
    recorded to block on → `check_deadlines` sees an active goal past its
    deadline and fires a REAL Stripe charge for OUR outage.
    """
    from app.workers import api_check

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, goal_type="api_endpoint")

            inconclusive = {
                "verification_status": vr.INCONCLUSIVE,
                "inconclusive_reason": reason,
                "verification_details": {
                    "url": "https://example.com/health",
                    "status_passed": False,
                    "inconclusive_detail": "we could not reach your endpoint",
                },
            }

            with (
                patch.object(
                    api_check,
                    "verify_api_endpoint",
                    new=AsyncMock(return_value=inconclusive),
                ),
                patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge,
            ):
                # No InconclusiveContractError may escape: on main this line
                # raises and nothing below it ever runs.
                result = await api_check.run_api_verification(
                    goal_id=goal.id,
                    submission_id=submission.id,
                    proof_data={},
                    criteria_data={"url": "https://example.com/health"},
                    db=db,
                )

            charge.assert_not_awaited()
            assert result["verification_status"] == vr.INCONCLUSIVE

            await db.refresh(submission)
            details = submission.verification_details
            assert details is not None, (
                "the outcome was never written; the submission would sit pending "
                "and the deadline sweep would charge the card"
            )
            assert details["outcome"] == vr.INCONCLUSIVE
            assert details["inconclusive_reason"] == reason
            # No verdict was reached, so the row records none (see the tests
            # above): `pending` here means "still ours to resolve", and the
            # blocked predicate is what stops the sweep.
            assert submission.verification_status == "pending"
            assert await vr.goal_verification_is_blocked(db, goal.id) is True
    finally:
        await engine.dispose()


async def test_api_verification_wire_carries_the_reason_without_a_caller_session():
    """The second call site — `db=None` — is a separate literal and was equally wrong."""
    from app.workers import api_check

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, goal_type="api_endpoint")

        inconclusive = {
            "verification_status": vr.INCONCLUSIVE,
            "inconclusive_reason": vr.REASON_UPSTREAM_UNAVAILABLE,
            "verification_details": {"url": "https://example.com/health"},
        }

        with (
            patch.object(
                api_check,
                "verify_api_endpoint",
                new=AsyncMock(return_value=inconclusive),
            ),
            patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge,
        ):
            await api_check.run_api_verification(
                goal_id=goal.id,
                submission_id=submission.id,
                proof_data={},
                criteria_data={"url": "https://example.com/health"},
            )

        charge.assert_not_awaited()

        async with factory() as db:
            # Re-read rather than refresh: the row was written by a session this
            # one knows nothing about, which is exactly the code path under test.
            row = await db.execute(
                text(
                    "SELECT verification_details FROM proof_submissions WHERE id = :id"
                ),
                {"id": submission.id},
            )
            details = row.scalar_one()
            assert details is not None, (
                "the outcome was never written; the submission would sit pending "
                "and the deadline sweep would charge the card"
            )
            assert details["outcome"] == vr.INCONCLUSIVE
            assert details["inconclusive_reason"] == vr.REASON_UPSTREAM_UNAVAILABLE
    finally:
        await engine.dispose()


async def test_api_verification_wire_leaves_a_verdict_reasonless():
    """The contract rejects a reason on a verdict, so the wire must not invent one."""
    from app.workers import api_check

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal, submission = await _make_goal(db, goal_type="api_endpoint")

            verdict = {
                "verification_status": vr.VERIFIED,
                "verification_details": {"url": "https://example.com/health"},
            }

            with (
                patch.object(
                    api_check,
                    "verify_api_endpoint",
                    new=AsyncMock(return_value=verdict),
                ),
                patch(CHARGE_BOUNDARY, new_callable=AsyncMock),
            ):
                result = await api_check.run_api_verification(
                    goal_id=goal.id,
                    submission_id=submission.id,
                    proof_data={},
                    criteria_data={"url": "https://example.com/health"},
                    db=db,
                )

            assert result["verification_status"] == vr.VERIFIED
            await db.refresh(submission)
            assert submission.verification_status == "verified"
    finally:
        await engine.dispose()
