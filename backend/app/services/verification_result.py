"""Shared persistence for verification-worker results.

Every goal-type worker previously carried an identical copy of
``_persist_result`` that updated the submission + goal and notified the user —
but none of them dispatched the pledge charge. A goal failed by verification
is terminal (submit-proof requires status ``active``) and the deadline sweep
only enforces ``active``/``pending_review`` goals, so the charge promised by
the failure notification never happened. Charging now lives here, on the same
path that marks the goal failed.

Three outcomes, not two
-----------------------
``failed`` bills a real card (``process_charge_for_goal`` →
``PaymentIntent.create(confirm=True, off_session=True)``). Verifiers now fail
closed, which is right — but "closed" was collapsing two different sentences
into one:

* "we checked, and you did not do the thing"  → the user failed; charge.
* "we could not check"                        → *we* failed; charging is theft.

A GitHub 502, an exhausted shared rate-limit quota, a Docker daemon restart, a
criteria type nobody ever implemented: none of those are the submitter's doing,
and every one of them used to arrive here as ``failed``. This module therefore
accepts a third outcome, ``inconclusive``, which can never reach the charge.

The boundary — where "our fault" ends
-------------------------------------
``inconclusive`` is admissible only when the fault is attributable to a
component *we* operate, and only via one of the codes in
``INCONCLUSIVE_REASONS``. There is no free-text escape hatch, because the
loophole this opens if blurred is total: a user who wants to dodge a pledge
just points the goal at something that reliably errors.

=========================== ===================================================
Reason code                 Admissible only for
=========================== ===================================================
UPSTREAM_UNAVAILABLE        A dependency we call returned 5xx, timed out, or
                            refused the connection (GitHub, YouTube, an
                            api_endpoint *we* own). NOT 404 (the resource does
                            not exist — the user's input), NOT 401/403
                            access-denied (the user's token/permissions).
UPSTREAM_RATE_LIMITED       A documented rate-limit signal on OUR shared quota
                            (e.g. ``x-ratelimit-remaining: 0``, or a body
                            saying the API rate limit is exceeded). A bare 403
                            is NOT sufficient: GitHub returns 403 both for
                            rate-limiting and for access-denied, and mapping
                            every 403 here re-opens the loophole in one line.
SANDBOX_INFRASTRUCTURE      The sandbox could not be built or was destroyed by
                            us: daemon/socket fault, image pull failure,
                            workspace setup failure, OOM-kill, SIGKILL from our
                            own backstop timer. NOT a non-zero exit from the
                            user's test command, and NOT a test suite that
                            legitimately fails.
CRITERIA_NOT_EVALUABLE      We accepted a goal whose criteria we cannot check
                            at all (unimplemented condition type, empty
                            criteria set). Ours because we accepted it. This is
                            permanent: retrying never helps, so it escalates
                            immediately (see ``dispatch_attempts`` below).
INTERNAL_ERROR              An unexpected exception inside our own
                            orchestration. NOT the catch-all for exceptions
                            raised while evaluating the user's artifact — if
                            the thing that broke is the user's repo, endpoint,
                            video or coordinates, the outcome is ``failed``.
=========================== ===================================================

Two structural properties keep that boundary from being blurred by accident:

1. **The charge decision reads exactly one variable.** It branches on
   ``status`` and never inspects ``details``. ``details`` is built from
   user-supplied values (repo URLs, condition results) and is echoed back
   through the verification-status endpoint, so it is untrusted for this
   purpose: a payload claiming ``outcome: inconclusive`` alongside
   ``status="failed"`` still charges.
2. **No hedging.** A top-level ``failure_reason`` in ``details`` is how every
   verifier in this codebase says "here is why *you* failed". Passing one
   together with ``inconclusive`` is a contradiction and raises. If a run
   evaluated one criterion and the user missed it, the outcome is ``failed``
   even if a second criterion was inconclusive — missing a criterion you *were*
   measured against is a real failure. Explanatory text for an inconclusive run
   goes in ``details["inconclusive_detail"]``.

What an inconclusive outcome does to the world
----------------------------------------------
As little as possible, on purpose: it leaves the goal *exactly* as an in-flight
verification would. The submission stays ``pending`` (the only non-verdict value
in the ``verification_status`` enum) and the goal keeps its status — normally
``active``, which is where it already was, because ``submit-proof`` does not
move it. So an inconclusive result introduces no goal state that the deadline
sweep was not already going to see, the owner can still submit another proof
(``_PROOF_ALLOWED_STATUSES == {"active"}``), and replaying the outcome is a
no-op.

Deliberately *not* ``pending_review``: that status is enforced by
``check_deadlines`` — charged five minutes past the deadline
(``app/workers/deadline.py``) — and it also closes the submission window, so it
would both bill for our outage and stop the user from retrying.

Retry, and where it stops
-------------------------
Staying ``pending`` under the attempt cap is exactly what
``app/workers/reconcile_dispatch.py`` claims, so a transient fault re-verifies
itself roughly every ``verification_dispatch_stale_minutes`` and resolves with
no operator involvement. ``dispatched_at`` is left as the just-completed
attempt set it, which is what spaces the retries out; nothing here shortens
that backoff. The loop is bounded by ``verification_dispatch_max_attempts`` —
the reconciler stops claiming the row — and a permanent reason skips the loop
entirely by saturating ``dispatch_attempts`` to the cap, so a criteria set that
can never be evaluated does not burn four attempts proving it.

Once no retry remains, ``details["needs_operator_review"]`` is set and
``goal_verification_is_blocked`` reports true for the goal. ``check_deadlines``
consults that predicate (``app/workers/deadline.py``) and skips the goal, so the
pledge is never auto-collected for a verification we could not complete. The
goal stays ``active`` rather than moving to a terminal status, because there is
no ``goal_status`` value meaning "blocked on us" — every existing one is either
a lie about the user's outcome or a free pass.

**The residual risk runs the other way, and it is not closed.** A blocked goal
is skipped on every sweep, forever, so an inconclusive outcome that nobody
resolves is indistinguishable from forgiving the pledge. ``needs_operator_review``
currently has no reader — no endpoint, no admin query, no alert — so "a human
will resolve it" is aspirational. Two consequences worth knowing before you
trust this path: a blocked *recurring* goal never spawns its next instance
(``_process_expired_goal`` returns before ``_create_next_recurring_instance``),
and the user is told a team is looking into it. Anything that widens what counts
as inconclusive widens uncollectable pledges — so keep the reason codes narrow,
and treat "who controls this input?" as the test, not "what were we debugging
when we found it?".
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.goal import Goal
from app.models.proof import ProofSubmission
from app.services.notification import create_notification, notify_goal_resolution

logger = logging.getLogger(__name__)

# ── Outcomes ───────────────────────────────────────────────────────────────
VERIFIED = "verified"
FAILED = "failed"
INCONCLUSIVE = "inconclusive"

# The two outcomes that are a judgement about the user and write through to
# ``proof_submissions.verification_status`` / ``goals.status``.
VERDICTS = frozenset({VERIFIED, FAILED})
OUTCOMES = VERDICTS | {INCONCLUSIVE}

# ── Inconclusive reason codes ──────────────────────────────────────────────
# Import these by name. A closed allowlist of module constants (rather than a
# string a caller composes) is the point: a typo is an AttributeError at import
# time instead of a reason code that quietly drifts out of the allowlist, and
# there is no spelling of "the user's repo 403'd" that lands in here.
REASON_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
REASON_UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
REASON_SANDBOX_INFRASTRUCTURE = "sandbox_infrastructure"
REASON_CRITERIA_NOT_EVALUABLE = "criteria_not_evaluable"
REASON_INTERNAL_ERROR = "internal_error"

# Retrying can plausibly reach a verdict.
TRANSIENT_REASONS = frozenset(
    {
        REASON_UPSTREAM_UNAVAILABLE,
        REASON_UPSTREAM_RATE_LIMITED,
        REASON_SANDBOX_INFRASTRUCTURE,
        REASON_INTERNAL_ERROR,
    }
)
# Retrying cannot: the same input will be un-evaluatable next time too.
PERMANENT_REASONS = frozenset({REASON_CRITERIA_NOT_EVALUABLE})
INCONCLUSIVE_REASONS = TRANSIENT_REASONS | PERMANENT_REASONS

# Keys this module owns inside ``verification_details``. Written last so a
# caller's dict cannot pre-empt them.
_OUTCOME_KEY = "outcome"
_REASON_KEY = "inconclusive_reason"
_RETRYABLE_KEY = "inconclusive_retryable"
_AT_KEY = "inconclusive_at"
_REVIEW_KEY = "needs_operator_review"
_MESSAGE_KEY = "user_message"

_USER_FACING_MESSAGE = (
    "We could not complete the check for this proof — the problem is on our "
    "side, not with what you submitted. You have not been charged."
)
_RETRY_SUFFIX = " We will try again automatically."
_REVIEW_SUFFIX = " Our team is looking into it."


class InconclusiveContractError(ValueError):
    """A caller used the inconclusive contract incorrectly.

    A subclass of ``ValueError`` so existing worker ``except Exception`` /
    ``except ValueError`` handling keeps working. Raising is the safe direction:
    no mutation has happened and no charge can follow.
    """


def _validate(status: str, details: dict, inconclusive_reason: str | None) -> None:
    """Reject malformed outcomes before anything is written or billed."""
    if status not in OUTCOMES:
        raise InconclusiveContractError(
            f"Unknown verification outcome {status!r}; expected one of "
            f"{sorted(OUTCOMES)}"
        )

    if status == INCONCLUSIVE:
        if inconclusive_reason not in INCONCLUSIVE_REASONS:
            raise InconclusiveContractError(
                f"status={INCONCLUSIVE!r} requires inconclusive_reason from "
                f"{sorted(INCONCLUSIVE_REASONS)}, got {inconclusive_reason!r}. "
                "Only faults in components we operate are inconclusive; a "
                "fault in the user's repo/endpoint/artifact is 'failed'."
            )
        if details.get("failure_reason"):
            raise InconclusiveContractError(
                "details['failure_reason'] states why the USER failed and "
                f"cannot be combined with status={INCONCLUSIVE!r}. If a "
                "criterion was actually measured and missed, the outcome is "
                "'failed'. Put explanatory text for an inconclusive run in "
                "details['inconclusive_detail']."
            )
    elif inconclusive_reason is not None:
        raise InconclusiveContractError(
            f"inconclusive_reason={inconclusive_reason!r} is meaningless with "
            f"status={status!r}; a verdict is not partly inconclusive."
        )


async def persist_verification_result(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    status: str,
    details: dict,
    *,
    inconclusive_reason: str | None = None,
) -> None:
    """Record a verification outcome, and charge the pledge iff the user failed.

    ``status`` is ``VERIFIED``, ``FAILED`` or ``INCONCLUSIVE``. The last one
    requires ``inconclusive_reason`` from ``INCONCLUSIVE_REASONS`` and never
    charges — see the module docstring for the boundary between the user's
    fault and ours.
    """
    details = details or {}
    # Before any mutation: a bad outcome must not half-write a row, and must
    # never fall through to a charge.
    _validate(status, details, inconclusive_reason)

    if status == INCONCLUSIVE:
        await _persist_inconclusive(
            db, goal_id, submission_id, details, inconclusive_reason
        )
        return

    # A settled submission is not re-decidable. The inconclusive path has
    # guarded this since it was written; the verdict path did not, and that
    # asymmetry is a wrongful charge: a replay (the reconciler, or a second
    # worker holding the same submission) that lands on `failed` after a run
    # already recorded `verified` would overwrite the verdict, commit
    # goal.status='failed', and charge a user who genuinely passed.
    #
    # `process_charge_for_goal` cannot save us here — it skips when
    # goal.status == "verified", but the block below sets the goal to failed
    # and commits BEFORE calling it.
    #
    # Expressed as a conditional UPDATE rather than read-then-write so two
    # concurrent verdicts cannot both observe an unsettled row: whichever
    # commits second matches zero rows and returns.
    claimed = await db.execute(
        sa_update(ProofSubmission)
        .where(
            ProofSubmission.id == submission_id,
            ProofSubmission.verification_status.notin_(tuple(VERDICTS)),
        )
        .values(verification_status=status, verification_details=details)
    )
    if claimed.rowcount == 0:
        # Zero rows means one of two different things, and they must not be
        # collapsed: a verdict is already recorded (stop — that is the replay
        # the claim exists to block), or there is no such submission row at all.
        existing = (
            await db.execute(
                select(ProofSubmission.verification_status).where(
                    ProofSubmission.id == submission_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await db.rollback()
            logger.info(
                "Ignoring %s verification for submission %s: already resolved as %s",
                status,
                submission_id,
                existing,
            )
            return
        # No row to annotate, but the verdict itself is still valid: the worker
        # reached it, and the goal's resolution + notification do not depend on
        # the submission row existing. Falling through keeps this case behaving
        # exactly as it did before the claim was introduced — collapsing it into
        # the replay branch silently stopped resolving the goal and stopped
        # notifying the owner (caught by tests/test_notifications.py).
        logger.warning(
            "No proof submission %s to record %s against; resolving goal %s anyway",
            submission_id,
            status,
            goal_id,
        )

    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if goal:
        goal.status = status
        # Notify the user their goal was resolved (verified/failed).
        await notify_goal_resolution(db, goal, status)

    await db.commit()

    if goal is not None and status == "failed":
        # Imported lazily: workers import this module, and payments imports
        # celery_app — keep the import cycle out of module import time.
        from app.workers.payments import process_charge_for_goal

        try:
            await process_charge_for_goal(str(goal.id), str(goal.user_id))
        except Exception:
            # The charge records its own failure state; never let a billing
            # error mask the verification result that was already committed.
            logger.exception("Charge processing failed for goal %s", goal.id)


async def _persist_inconclusive(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    details: dict,
    reason: str,
) -> None:
    """Record "we could not check", touching nothing that leads to a charge.

    There is no code path from here to ``process_charge_for_goal``: this
    function is the whole handling of the outcome and it returns to a caller
    that has already branched away from the charge.
    """
    result = await db.execute(
        select(ProofSubmission).where(ProofSubmission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        # Nothing to annotate. Notably NOT an error: the row may have been
        # deleted with its goal while a worker was in flight.
        logger.warning(
            "Inconclusive verification (%s) for unknown submission %s",
            reason,
            submission_id,
        )
        return

    if submission.verification_status in VERDICTS:
        # A verdict already landed — most likely this is the reconciler
        # replaying a submission that a concurrent worker just resolved.
        # Downgrading it to pending would re-open a settled goal and make it
        # claimable again, and on a `failed` row the replay could reach a second
        # charge. Leave it alone.
        logger.info(
            "Ignoring inconclusive verification (%s) for submission %s: "
            "already resolved as %s",
            reason,
            submission_id,
            submission.verification_status,
        )
        return

    previous = submission.verification_details or {}
    was_inconclusive = previous.get(_OUTCOME_KEY) == INCONCLUSIVE
    was_in_review = bool(previous.get(_REVIEW_KEY))

    retryable = reason in TRANSIENT_REASONS
    cap = settings.verification_dispatch_max_attempts
    attempts = submission.dispatch_attempts or 0
    if not retryable and attempts < cap:
        # Use the reconciler's own bound as the "stop re-dispatching" signal
        # rather than inventing a column: re-running a check we have no
        # implementation for cannot produce a different answer, and the row
        # should reach an operator now instead of in four staleness windows.
        submission.dispatch_attempts = cap
        attempts = cap
    needs_review = attempts >= cap

    message = _USER_FACING_MESSAGE + (
        _RETRY_SUFFIX if not needs_review else _REVIEW_SUFFIX
    )

    # ``pending`` is already the value on an unresolved row; set it explicitly
    # so this function is correct no matter how it is reached.
    submission.verification_status = "pending"
    annotated = dict(details)
    annotated[_OUTCOME_KEY] = INCONCLUSIVE
    annotated[_REASON_KEY] = reason
    annotated[_RETRYABLE_KEY] = retryable
    annotated[_AT_KEY] = datetime.now(timezone.utc).isoformat()
    annotated[_REVIEW_KEY] = needs_review
    annotated[_MESSAGE_KEY] = message
    submission.verification_details = annotated

    # The goal is deliberately untouched: no verdict was reached, so its status
    # stays whatever an in-flight verification would have left it as.
    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()

    logger.warning(
        "Verification inconclusive for goal %s submission %s: reason=%s "
        "retryable=%s attempts=%s/%s needs_operator_review=%s",
        goal_id,
        submission_id,
        reason,
        retryable,
        attempts,
        cap,
        needs_review,
    )

    # Tell the owner once per state, not once per retry: an outage that burns
    # the whole attempt cap should produce "still checking" and then "we are on
    # it", never four identical notifications.
    should_notify = goal is not None and (
        not was_inconclusive or (needs_review and not was_in_review)
    )
    if should_notify:
        await create_notification(
            db,
            user_id=goal.user_id,
            # ``notification_type`` is a Postgres enum with no value for this
            # (app/models/notification.py:23); ``proof_received`` is the
            # closest true statement — the proof is in, the check is not done.
            notification_type="proof_received",
            title=f"Still verifying: {goal.title}",
            body=message,
            goal_id=goal.id,
        )

    await db.commit()


async def goal_verification_is_blocked(db: AsyncSession, goal_id: uuid.UUID) -> bool:
    """True when this goal has no verdict *because of us*.

    The goal's most recent submission ended inconclusive and is still
    unresolved: either a reconciler retry is pending or the attempt cap is
    spent and an operator needs to look. Charging such a goal bills the user
    for our outage.

    Intended for ``check_deadlines`` (``app/workers/deadline.py``), which
    currently charges any ``active``/``pending_review`` goal whose deadline has
    passed without asking why verification never finished; and for operators
    triaging ``needs_operator_review`` rows.
    """
    result = await db.execute(
        select(ProofSubmission)
        .where(ProofSubmission.goal_id == goal_id)
        .order_by(ProofSubmission.submitted_at.desc())
        .limit(1)
    )
    submission = result.scalar_one_or_none()
    if submission is None or submission.verification_status in VERDICTS:
        return False
    details = submission.verification_details or {}
    return details.get(_OUTCOME_KEY) == INCONCLUSIVE
